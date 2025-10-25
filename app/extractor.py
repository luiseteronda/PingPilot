import re, hashlib
from bs4 import BeautifulSoup
from typing import Optional
import requests
from playwright.sync_api import sync_playwright

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"

def fetch_html(
    url: str,
    render_js: bool = False,
    wait_selector: Optional[str] = None,
    wait_until: str = "networkidle",
    timeout_ms: int = 15000,
    user_agent: str = DEFAULT_UA,
) -> str:
    if not render_js:
        r = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
        r.raise_for_status()
        return r.text

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=user_agent, java_script_enabled=True)
        page = context.new_page()
        page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        if wait_selector:
            page.wait_for_selector(wait_selector, timeout=timeout_ms)
        html = page.content()
        context.close()
        browser.close()
        return html

def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(" ", strip=True)

JUNK_SELECTORS = [
    "[role='banner']", "header", "nav", "footer", "[aria-label='Footer']",
    "[data-testid='cookie-banner']", "[id*='cookie']", "[class*='cookie']",
    "[id*='consent']", "[class*='consent']",
    "[id*='subscribe']", "[class*='subscribe']",
    "[id*='signup']", "[class*='signup']",
    ".modal", ".toast", ".overlay"
]

def strip_junk_soup(soup: BeautifulSoup):
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    for sel in JUNK_SELECTORS:
        for el in soup.select(sel):
            el.decompose()

def normalize_text_block(s: str) -> str:
    import re
    return re.sub(r"\s+", " ", (s or "")).strip()

def normalize_lines_join(texts: list[str]) -> str:
    clean = []
    for t in texts:
        t = re.sub(r"\s+", " ", t).strip()
        if t: clean.append(t)
    return "\n".join(clean)

def css_path(el):
    parts = []
    while el and getattr(el, "name", None) and el.name != "html":
        idx = 1; sib = el
        while sib.previous_sibling:
            sib = sib.previous_sibling
            if getattr(sib, "name", None) == el.name: idx += 1
        parts.append(f"{el.name}:nth-of-type({idx})")
        el = el.parent
    return " > ".join(reversed(parts))

def block(text, type_, path, weight):
    return {"type": type_, "text": text, "path": path, "weight": weight,
            "hash": hashlib.sha256(text.encode("utf-8")).hexdigest()}

HEADING_SEL = "main h1, main h2, main h3, article h1, article h2, article h3, [role='heading']"
PARA_SEL = "main p, article p"
LIST_SEL = "main li, article li"
PRICE_RE = re.compile(r"(?<!\w)([$€£]\s?\d[\d\s.,]*|\d[\d\s.,]*\s?(USD|EUR|GBP|€|£))(?!\w)", re.I)
DATE_RE  = re.compile(r"\b(20\d{2}|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", re.I)

def extract_blocks_from_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    strip_junk_soup(soup)
    blocks = []

    for el in soup.select(HEADING_SEL):
        t = normalize_text_block(el.get_text(" ", strip=True))
        if t:
            lvl = {"H1": 10, "H2": 8, "H3": 6}.get(el.name.upper(), 5)
            blocks.append(block(t, "headline", css_path(el), lvl))

    for el in soup.select(PARA_SEL):
        t = normalize_text_block(el.get_text(" ", strip=True))
        if t and len(t) > 40:
            blocks.append(block(t, "paragraph", css_path(el), 4))

    for el in soup.select(LIST_SEL):
        t = normalize_text_block(el.get_text(" ", strip=True))
        if t and len(t) > 10:
            blocks.append(block(t, "list_item", css_path(el), 5))

    full = normalize_text_block(soup.get_text(" ", strip=True))
    for m in PRICE_RE.finditer(full):
        blocks.append(block(m.group(0), "price", "text-scan", 9))
    for m in DATE_RE.finditer(full):
        blocks.append(block(m.group(0), "date", "text-scan", 3))

    seen, uniq = set(), []
    for b in blocks:
        k = (b["type"], b["text"])
        if k not in seen:
            seen.add(k); uniq.append(b)
    return uniq

def index_blocks(blocks: list[dict]) -> dict:
    idx = {}
    for b in blocks:
        idx.setdefault(b["path"], []).append(b)
    return idx

def diff_blocks(prev: list[dict], curr: list[dict]) -> dict:
    prev_idx, curr_idx = index_blocks(prev), index_blocks(curr)
    added, removed, modified = [], [], []

    for path, cur_blocks in curr_idx.items():
        pv = prev_idx.get(path)
        if not pv:
            added.extend(cur_blocks)
        else:
            prev_hashes = {b["hash"] for b in pv}
            for cb in cur_blocks:
                if cb["hash"] not in prev_hashes:
                    modified.append(cb)

    for path, pv in prev_idx.items():
        if path not in curr_idx:
            removed.extend(pv)

    return {"added": added, "removed": removed, "modified": modified}

def filter_relevant(changes: dict, keep_max=60) -> dict:
    def score(b):
        base = b.get("weight", 1)
        t = b.get("type")
        if t == "price": base += 5
        if t == "headline": base += 3
        if t == "list_item": base += 2
        if len(b["text"]) < 12: base -= 2
        return base
    out = {}
    for k in ["added", "removed", "modified"]:
        items = sorted(changes.get(k, []), key=score, reverse=True)
        out[k] = items[:keep_max]
    return out
