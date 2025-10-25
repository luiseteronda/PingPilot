import os, json, difflib
from types import SimpleNamespace
from datetime import datetime, timezone
from typing import List, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from sqlmodel import Session, select, delete

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

from .db import engine, create_db, Monitor, CheckResult
from .renderer import fetch_html_and_screenshot
from .extractor import extract_blocks_from_html, filter_relevant
from .diffing import normalize_text_block, make_diff_preview, text_hash, image_hash, is_allowed_by_robots
from .notify import send_slack, send_email, render_change_email
from .scheduler import scheduler, schedule_monitor, load_schedules
from .llm_change import build_gemini_chain

app = FastAPI(title="PingPilot", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# --------- Schemas ---------
class MonitorIn(BaseModel):
    url: HttpUrl
    selector: Optional[str] = None
    render_js: bool = False
    frequency_minutes: int = 60
    ignore_robots: bool = False

class ResultOut(BaseModel):
    id: int
    checked_at: datetime
    changed_text: bool
    changed_visual: bool
    change_ratio: float
    diff_preview: str
    note: str
    material_change: bool
    severity: str
    semantic_summary: str

class MonitorOut(BaseModel):
    id: int
    url: str
    selector: Optional[str]
    frequency_minutes: int
    render_js: bool
    last_checked_at: Optional[datetime]
    is_active: bool
    ignore_robots: bool = False
    class Config: orm_mode = True

# --------- LLM wrapper ---------
LLM_ENABLED = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

def llm_decide_from_blocks(url: str, filtered_changes: dict):
    def pack(items, n):
        items = items or []
        return [{"type": i.get("type",""), "text": i.get("text","")[:500]} for i in items[:n]]

    payload = {
        "url": url,
        "added":   json.dumps(pack(filtered_changes.get("added"),   20)),
        "removed": json.dumps(pack(filtered_changes.get("removed"), 20)),
        "modified":json.dumps(pack(filtered_changes.get("modified"),10)),
    }

    if not LLM_ENABLED:
        # no-op so checks never fail if key is missing
        return SimpleNamespace(material_change=False, severity="none",
                               summary_short="", json=lambda: "{}")

    try:
        built = build_gemini_chain(model="gemini-1.5-flash")
        # handle both return shapes: chain OR (prompt, llm, parser)
        if isinstance(built, tuple):
            prompt, llm, parser = built
            chain = prompt | llm | parser
        else:
            chain = built
        return chain.invoke(payload)
    except Exception as e:
        print("[LLM ERROR]", e)
        return SimpleNamespace(material_change=False, severity="none",
                               summary_short="", json=lambda: "{}")

# --------- Core check ---------
def run_check(monitor_id: int):
    with Session(engine) as session:
        mon = session.get(Monitor, monitor_id)
        if not mon or not mon.is_active: return

        if (not mon.ignore_robots) and (not is_allowed_by_robots(mon.url)):
            session.add(CheckResult(monitor_id=mon.id, note="Blocked by robots.txt"))
            session.commit(); return

        try:
            text_for_diff, screenshot, raw_html = fetch_html_and_screenshot(mon.url, mon.render_js, mon.selector)
            text_for_diff = normalize_text_block(text_for_diff)
            raw_text_len = len(text_for_diff or "")
            norm_text_len = raw_text_len

            old_text = mon.last_text or ""
            new_text_hash = text_hash(text_for_diff)
            text_changed = (mon.last_content_hash is not None) and (new_text_hash != mon.last_content_hash)

            visual_changed, new_img_hash = False, None
            if screenshot:
                new_img_hash = image_hash(screenshot)
                visual_changed = (mon.last_image_hash is not None) and (new_img_hash != mon.last_image_hash)

            diff_preview = make_diff_preview(old_text, text_for_diff) if text_changed else ""
            change_ratio = difflib.SequenceMatcher(a=old_text, b=text_for_diff).ratio() if text_changed else 0.0

            new_blocks = extract_blocks_from_html(raw_html) if raw_html else []
            prev_blocks = json.loads(mon.last_blocks_json or "[]")
            filtered = filter_relevant({"added":[], "removed":[], "modified":[]})
            if new_blocks:
                from .extractor import diff_blocks
                filtered = filter_relevant(diff_blocks(prev_blocks, new_blocks))

            added_lines = [b["text"] for b in filtered.get("added", [])][:12]
            removed_lines = [b["text"] for b in filtered.get("removed", [])][:12]

            likely_changed = text_changed or visual_changed or any(filtered.values())
            material_change, severity, summary_short, llm_json_str = False, "none", "", ""
            if likely_changed:
                try:
                    llm_out = llm_decide_from_blocks(mon.url, filtered)
                    material_change = bool(getattr(llm_out, "material_change", False))
                    severity = getattr(llm_out, "severity", "none") or "none"
                    summary_short = getattr(llm_out, "summary_short", "") or ""
                    llm_json_str = llm_out.json() if hasattr(llm_out, "json") else json.dumps(llm_out)
                except Exception as e:
                    print("[LLM ERROR]", e)

            session.add(CheckResult(
                monitor_id=mon.id,
                status_code=200,
                blocked_reason="",
                raw_text_len=raw_text_len,
                norm_text_len=norm_text_len,
                text_change_ratio=change_ratio,
                changed_text=bool(text_changed),
                changed_visual=bool(visual_changed),
                diff_preview=diff_preview or summary_short or "",
                visual_hamming=0,
                material_change=material_change,
                severity=severity,
                semantic_summary=summary_short or "",
                llm_json=llm_json_str or "",
                changes_json=json.dumps(filtered) if filtered else "[]",
                note=""
            ))

            mon.last_content_hash = new_text_hash
            mon.last_text = text_for_diff
            if new_img_hash: mon.last_image_hash = new_img_hash
            mon.last_blocks_json = json.dumps(new_blocks) if new_blocks else "[]"
            mon.last_checked_at = datetime.now(timezone.utc)
            session.add(mon); session.commit()

            if material_change or severity in ("medium","high") or text_changed or visual_changed:
                from .notify import render_change_email, send_email, send_slack
                subject = f"[{(severity or 'info').upper()}] Change: {mon.url}"
                body = render_change_email(mon, severity, summary_short, added_lines, removed_lines)
                send_email(subject, body)
                summary_line = summary_short or (added_lines[0] if added_lines else "Change detected")
                send_slack(f"{(severity or 'info').upper()} | {mon.url} — {summary_line}")

        except Exception as e:
            session.add(CheckResult(monitor_id=mon.id, note=f"Error: {e}")); session.commit()
            print(f"[ERROR] Monitor {mon.id} {mon.url}: {e}")

# --------- Startup / API ---------
@app.on_event("startup")
def on_startup():
    create_db()
    scheduler.start()
    load_schedules(run_check)
    print("[PingPilot] ready.")

@app.on_event("shutdown")
def on_shutdown():
    scheduler.shutdown()

class MonitorOut(BaseModel):
    id:int; url:str; selector:Optional[str]; frequency_minutes:int; render_js:bool
    last_checked_at: Optional[datetime]; is_active: bool; ignore_robots: bool = False
    class Config: orm_mode=True

@app.get("/health")
def health(): return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}

@app.post("/monitors", response_model=MonitorOut)
def create_monitor(m: MonitorIn):
    from sqlmodel import Session
    from .scheduler import schedule_monitor
    if m.frequency_minutes < 5: raise HTTPException(status_code=400, detail="frequency_minutes must be >= 5")
    rec = Monitor(url=str(m.url), selector=m.selector, render_js=bool(m.render_js),
                  frequency_minutes=m.frequency_minutes, ignore_robots=m.ignore_robots)
    with Session(engine) as session:
        session.add(rec); session.commit(); session.refresh(rec)
    schedule_monitor(rec, run_check); run_check(rec.id)
    return MonitorOut(id=rec.id, url=rec.url, selector=rec.selector, render_js=rec.render_js,
                      frequency_minutes=rec.frequency_minutes, last_checked_at=rec.last_checked_at,
                      is_active=rec.is_active, ignore_robots=rec.ignore_robots)

@app.get("/monitors", response_model=List[MonitorOut])
def list_monitors():
    with Session(engine) as session:
        mons = session.exec(select(Monitor)).all()
    return [MonitorOut(id=m.id, url=m.url, selector=m.selector, render_js=m.render_js,
                       frequency_minutes=m.frequency_minutes, last_checked_at=m.last_checked_at,
                       is_active=m.is_active, ignore_robots=m.ignore_robots) for m in mons]

@app.post("/monitors/{monitor_id}/run")
def run_now(monitor_id: int): run_check(monitor_id); return {"ok": True}

@app.post("/monitors/{monitor_id}/toggle")
def toggle_monitor(monitor_id: int):
    with Session(engine) as session:
        mon = session.get(Monitor, monitor_id)
        if not mon: raise HTTPException(status_code=404, detail="Not found")
        mon.is_active = not mon.is_active; session.add(mon); session.commit()
    try:
        from .scheduler import scheduler
        if mon.is_active: schedule_monitor(mon, run_check)
        else: scheduler.remove_job(f"monitor-{mon.id}")
    except Exception: pass
    return {"id": monitor_id, "is_active": mon.is_active}

@app.get("/monitors/{monitor_id}/results")
def list_results(monitor_id: int, limit: int = 5):
    with Session(engine) as session:
        rows = session.exec(select(CheckResult)
                .where(CheckResult.monitor_id == monitor_id)
                .order_by(CheckResult.checked_at.desc()).limit(limit)).all()
    return [{
        "id": r.id, "checked_at": r.checked_at,
        "changed_text": r.changed_text, "changed_visual": r.changed_visual,
        "change_ratio": r.text_change_ratio, "diff_preview": r.diff_preview,
        "note": r.note, "material_change": r.material_change,
        "severity": r.severity, "semantic_summary": r.semantic_summary
    } for r in rows]

@app.delete("/monitors/{monitor_id}")
def delete_monitor(monitor_id: int):
    # stop the scheduled job (if any)
    try:
        from .scheduler import scheduler
        scheduler.remove_job(f"monitor-{monitor_id}")
    except Exception:
        pass

    # delete DB rows safely (child rows first)
    with Session(engine) as session:
        mon = session.get(Monitor, monitor_id)
        if not mon:
            raise HTTPException(status_code=404, detail="Monitor not found")

        session.exec(delete(CheckResult).where(CheckResult.monitor_id == monitor_id))
        session.delete(mon)
        session.commit()

    # don't return ORM objects (avoids DetachedInstanceError)
    return {"ok": True, "id": monitor_id}