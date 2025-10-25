import os, ssl, json, requests, smtplib
from typing import List
from html import escape
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT") or "587")
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
ALERT_FROM = os.getenv("ALERT_FROM", "PingPilot <alerts@localhost>")
ALERT_TO = os.getenv("ALERT_TO")

def send_slack(text: str):
    if not SLACK_WEBHOOK_URL: return
    try:
        requests.post(SLACK_WEBHOOK_URL,
                      headers={"Content-Type": "application/json"},
                      data=json.dumps({"text": text}), timeout=10)
    except Exception as e:
        print("[SLACK ERROR]", e)

def send_email(subject: str, html_body: str):
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and ALERT_TO):
        print(f"[ALERT] {subject}\n{html_body}\n(Email not configured; logging only.)")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject; msg["From"] = ALERT_FROM; msg["To"] = ALERT_TO
    msg.attach(MIMEText(html_body, "html"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls(context=ctx); server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(ALERT_FROM, [ALERT_TO], msg.as_string())

def render_change_email(mon, severity: str, summary_short: str,
                        added: List[str], removed: List[str], dashboard_url: str | None = None) -> str:
    added = (added or [])[:6]; removed = (removed or [])[:6]
    def li(items, sign): return "".join(f"<li>{sign} {escape(it)}</li>" for it in items)
    dash = f' • <a href="{dashboard_url}">Open in dashboard</a>' if dashboard_url else ""
    selector = escape(mon.selector or "(auto)"); url = escape(mon.url); sev = escape((severity or "info").upper())
    summary = escape(summary_short or "Change detected")
    return f"""
      <h3 style="margin:0 0 6px 0;">Change detected</h3>
      <p style="margin:0 0 8px 0;">
        <b>Severity:</b> {sev} • <b>URL:</b> <a href="{url}">{url}</a> •
        <b>Selector:</b> {selector}{dash}
      </p>
      <p style="margin:0 0 8px 0;"><b>Summary:</b> {summary}</p>
      <div style="margin:8px 0 0 0;">
        {"<p><b>Added</b></p><ul style='margin:4px 0 8px 18px'>" + li(added, "➕") + "</ul>" if added else ""}
        {"<p><b>Removed</b></p><ul style='margin:4px 0 0 18px'>" + li(removed, "➖") + "</ul>" if removed else ""}
      </div>
    """
