import re, io, difflib, hashlib
from urllib import robotparser
from PIL import Image

def normalize_text_block(s: str) -> str:
    import re
    return re.sub(r"\s+", " ", (s or "")).strip()

def make_diff_preview(old_text: str, new_text: str, max_lines: int = 80) -> str:
    diff_lines = list(difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(), lineterm="", n=2
    ))
    head = diff_lines[:max_lines]
    return "\n".join(head) if head else ""

def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def image_hash(png_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(png_bytes)).convert("L").resize((64, 64))
    return hashlib.sha256(img.tobytes()).hexdigest()

def is_allowed_by_robots(url: str, user_agent: str = "PingPilotBot") -> bool:
    import re
    try:
        base = re.match(r"^https?://[^/]+", url).group(0)  # type: ignore
    except Exception:
        return True
    rp = robotparser.RobotFileParser()
    rp.set_url(f"{base}/robots.txt")
    try: rp.read()
    except Exception: return True
    return rp.can_fetch(user_agent, url)
