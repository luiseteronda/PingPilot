import re, difflib, io, hashlib
from bs4 import BeautifulSoup
from PIL import Image

# ---- Config knobs ----
TEXT_CHANGE_THRESHOLD = 0.01      # 1% normalized diff
VISUAL_HAMMING_THRESHOLD = 8      # tune after sampling
DIFF_MAX_CHARS = 20000            # keep previews small

def normalize_text(t: str) -> str:
    if not t: return ""
    t = re.sub(r"\u00a0", " ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()

def extract_blocks(html: str, selector: str = "") -> tuple[str, list[str]]:
    """Return (main_text, blocks_list) using selector->main->fallback strategy."""
    if not html: return "", []
    soup = BeautifulSoup(html, "html.parser")

    # 1) user selector (supports comma list)
    if selector:
        parts = []
        for sel in [s.strip() for s in selector.split(",") if s.strip()]:
            parts += [el.get_text(" ", strip=True) for el in soup.select(sel)]
        text = normalize_text("\n".join(p for p in parts if p))
        if text:
            return text, [t for t in parts if t]

    # 2) <main> heuristic
    main = soup.find("main")
    if main:
        txt = normalize_text(main.get_text(" ", strip=True))
        if txt:
            return txt, [txt]

    # 3) largest text block heuristic
    candidates = sorted(
        (el.get_text(" ", strip=True) for el in soup.find_all(["article","section","div","p"])),
        key=lambda s: len(s or ""), reverse=True
    )[:8]
    txt = normalize_text("\n".join(candidates))
    return txt, [normalize_text(c) for c in candidates if c]

def text_change_ratio(prev: str, curr: str) -> float:
    r = difflib.SequenceMatcher(None, prev, curr).ratio()
    return round(1.0 - r, 4)

def html_diff_preview(old: str, new: str) -> str:
    # word-ish diff → simple HTML
    sm = difflib.SequenceMatcher(None, old, new)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        a, b = old[i1:i2], new[j1:j2]
        if op == "equal": out.append(a)
        elif op == "delete": out.append(f"<del>{a}</del>")
        elif op == "insert": out.append(f"<ins>{b}</ins>")
        elif op == "replace": out.append(f"<del>{a}</del><ins>{b}</ins>")
        if sum(len(x) for x in out) > DIFF_MAX_CHARS: break
    return "".join(out)

# ---- Visual hashing (aHash) ----
def ahash(image_bytes: bytes, size: int = 8) -> int:
    if not image_bytes: return 0
    im = Image.open(io.BytesIO(image_bytes)).convert("L").resize((size, size))
    pixels = list(im.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for i, p in enumerate(pixels):
        if p > avg:
            bits |= 1 << i
    return bits

def hamming64(a: int, b: int) -> int:
    return (a ^ b).bit_count()

# ---- Material-change classifier ----
PRICE_RE = re.compile(r"(\$|€|£)\s?\d[\d,\.]*")
DATE_RE  = re.compile(r"\b(\d{1,2}[/.-]\d{1,2}([/.-]\d{2,4})?|(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b)", re.I)
STOCK_RE = re.compile(r"\b(in stock|out of stock|unavailable|pre[- ]?order)\b", re.I)
CTA_RE   = re.compile(r"\b(buy now|add to cart|book now|subscribe)\b", re.I)

def classify_material_change(old: str, new: str) -> tuple[bool, str]:
    changed_price = bool(PRICE_RE.search(old) != PRICE_RE.search(new) or PRICE_RE.findall(old) != PRICE_RE.findall(new))
    changed_date  = bool(DATE_RE.search(old) != DATE_RE.search(new))
    changed_stock = bool(STOCK_RE.search(old) != STOCK_RE.search(new))
    changed_cta   = bool(CTA_RE.search(old) != CTA_RE.search(new))

    if changed_stock or changed_price: return True, "high"
    if changed_date: return True, "medium"
    if changed_cta:  return True, "low"
    return False, "none"
