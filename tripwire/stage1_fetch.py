import json
import os
import re

from . import config

# --- Optional Web/Doc imports ---
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium_stealth import stealth
except Exception:
    webdriver = None
    ChromeService = None
    WebDriverWait = None
    EC = None
    By = None
    ChromeDriverManager = None
    stealth = None

from bs4 import BeautifulSoup
from markdownify import markdownify as md
import docx


def initialize_driver():
    """
    Initializes a headless Chrome driver with stealth settings to bypass anti-bot detection.
    """
    if webdriver is None:
        raise RuntimeError("Selenium/webdriver dependencies not available in this environment.")
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)
    if stealth:
        stealth(driver, languages=["en-US", "en"], vendor="Google Inc.", platform="Win32", fix_hairline=True)
    return driver


def clean_html_content(html: str) -> str:
    """
    Strips non-essential HTML tags (nav, footer, etc.) and removes dynamic timestamps.
    """
    soup = BeautifulSoup(html, 'html.parser')
    body = soup.body
    if not body:
        return ""
    for selector in config.TAGS_TO_EXCLUDE:
        for tag in body.select(selector):
            tag.decompose()
    text = str(body)
    text = re.sub(r'Generated on:? \d{1,2}/\d{1,2}/\d{4}.*', '', text, flags=re.IGNORECASE)
    return text


def fetch_webpage_content(driver, url):
    """
    Uses Selenium to fetch a webpage, wait for rendering, and convert to Markdown.
    """
    driver.get(url)
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
    cleaned_html = clean_html_content(driver.page_source)
    return md(cleaned_html, heading_style="ATX")


def sanitize_rss(xml_content):
    """
    Normalizes RSS XML by stripping transient channel-level dates and sorting items by GUID.
    """
    soup = BeautifulSoup(xml_content, 'xml')
    for tag in ['lastBuildDate', 'pubDate', 'generator']:
        t = soup.find(tag)
        if t and t.parent and t.parent.name == 'channel':
            t.decompose()
    items = soup.find_all('item')
    items.sort(key=lambda x: x.find('guid').text if x.find('guid') else (x.find('link').text if x.find('link') else ''))
    channel = soup.find('channel')
    if channel:
        for item in soup.find_all('item'):
            item.extract()
        for item in items:
            channel.append(item)
    return soup.prettify()


def fetch_legislation_metadata(session, source):
    """
    Fetches latest document metadata from the Federal Legislation OData API.
    """
    params = {"$filter": f"titleid eq '{source['title_id']}'", "$orderby": "start desc", "$top": "1"}
    resp = session.get(source['base_url'], params=params, timeout=30)
    resp.raise_for_status()
    val = resp.json().get('value', [])
    if not val:
        return None, None
    meta = val[0]
    return meta.get('registerId'), meta


def _extract_docx_text(docx_path: str) -> str:
    d = docx.Document(docx_path)
    lines = []
    for para in d.paragraphs:
        t = (para.text or '').strip()
        if t:
            lines.append(t)
    return "\n\n".join(lines)


def download_legislation_content(session, base_url, meta):
    candidate_urls = []
    for k in ['download', 'downloadUrl', 'Download', 'DownloadUrl', 'url', 'Url']:
        v = meta.get(k)
        if isinstance(v, str) and v.startswith('http'):
            candidate_urls.append(v)

    for k in ['documents', 'Documents', 'files', 'Files']:
        docs = meta.get(k)
        if isinstance(docs, list):
            for item in docs:
                if isinstance(item, dict):
                    for kk in ['downloadUrl', 'url', 'href']:
                        v = item.get(kk)
                        if isinstance(v, str) and v.startswith('http'):
                            candidate_urls.append(v)

    for url in candidate_urls:
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            ctype = (r.headers.get('Content-Type') or '').lower()

            if 'word' in ctype or url.lower().endswith('.docx'):
                tmp = os.path.join(config.OUTPUT_DIR, "_tmp_legislation_download.docx")
                with open(tmp, 'wb') as f:
                    f.write(r.content)
                text = _extract_docx_text(tmp)
                try:
                    os.remove(tmp)
                except Exception:
                    pass
                return text

            if 'html' in ctype:
                return md(clean_html_content(r.text), heading_style="ATX")

            try:
                return r.text
            except Exception:
                pass
        except Exception as e:
            config.logger.warning(f"Legislation download candidate failed {url}: {e}")

    return json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False)
