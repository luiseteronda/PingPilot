import requests
from typing import Optional
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from .extractor import strip_junk_soup, normalize_text_block, normalize_lines_join

def fetch_html_and_screenshot(url: str, render_js: bool, selector: Optional[str]):
    """
    Returns (text_for_diff, screenshot_bytes or None, raw_html).
    """
    def extract_headlines_soup(soup: BeautifulSoup) -> str:
        strip_junk_soup(soup)
        candidates = soup.select("main h2, main h3") or soup.select("article h1, article h2, article h3") or soup.select("h2, h3")
        texts = [normalize_text_block(c.get_text(" ", strip=True)) for c in candidates]
        seen, uniq = set(), []
        for t in texts:
            if t and t not in seen:
                seen.add(t); uniq.append(t)
        return normalize_lines_join(uniq[:100]) or normalize_text_block(soup.get_text(" ", strip=True))

    if render_js:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page()
            page.set_default_timeout(25000)
            page.goto(url, wait_until="load")
            # strip common overlays in DOM
            page.evaluate("""(sels)=>{ for (const s of sels) document.querySelectorAll(s).forEach(e=>e.remove()); }""",
                          ["#cookie",".cookie","#consent",".consent","header","nav","footer"])
            screenshot = page.screenshot(full_page=True)
            if selector:
                try:
                    loc = page.locator(selector)
                    count = loc.count()
                    items = []
                    for i in range(min(count, 200)):
                        try:
                            items.append(normalize_text_block(loc.nth(i).inner_text(timeout=3000)))
                        except Exception:
                            pass
                    if items:
                        raw_html = page.content()
                        browser.close()
                        return normalize_lines_join(items), screenshot, raw_html
                except Exception:
                    pass
            raw_html = page.content()
            browser.close()
            soup = BeautifulSoup(raw_html, "lxml")
            return extract_headlines_soup(soup), screenshot, raw_html

    r = requests.get(url, timeout=20, headers={"User-Agent": "PingPilotBot/0.1"})
    r.raise_for_status()
    raw_html = r.text
    soup = BeautifulSoup(raw_html, "lxml")
    strip_junk_soup(soup)
    if selector:
        nodes = soup.select(selector)
        if nodes:
            items = [normalize_text_block(n.get_text(" ", strip=True)) for n in nodes]
            return normalize_lines_join(items), None, raw_html
    return extract_headlines_soup(soup), None, raw_html
