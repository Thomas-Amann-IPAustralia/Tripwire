import os
import json
import hashlib
import logging
import re
import sys
import shutil
import time
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

from selenium import webdriver
from selenium_stealth import stealth
from bs4 import BeautifulSoup
import google.generativeai as genai
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

# --- Configuration Constants ---
POLICY_SETS_FILE = 'policy_sets.json'
HASHES_FILE = 'hashes.json'
SNAPSHOTS_DIR = 'snapshots'
ANALYSIS_DIR = 'analysis'
LOG_DIR = 'logs'

GEMINI_MODEL = 'gemini-2.5-flash'
AEST_TZ = timezone(timedelta(hours=10))
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 5
PAGE_LOAD_TIMEOUT = 25

FAILURE_SIGNATURES = [
    "This site can't be reached",
    "ERR_HTTP2_PROTOCOL_ERROR",
    "Enable JavaScript and cookies to continue",
    "Checking if the site connection is secure",
    "Just a moment...",
    "Verifying you are human",
    "DDoS protection by Cloudflare",
    "Access denied",
]

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# --- Helper Functions ---

def setup_directories() -> None:
    for dir_path in [SNAPSHOTS_DIR, ANALYSIS_DIR, LOG_DIR]:
        os.makedirs(dir_path, exist_ok=True)


def slugify_set_name(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9\-]+', '_', name).strip('_')


def generate_md5(text: str) -> str:
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def load_json_file(file_path: str):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                log.warning("Failed to parse %s, using default", file_path)
                return {} if file_path == HASHES_FILE else []
    return {} if file_path == HASHES_FILE else []


def save_json_file(data, file_path: str) -> None:
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def save_snapshot(file_id: str, content: str) -> None:
    with open(os.path.join(SNAPSHOTS_DIR, f"{file_id}.txt"), 'w', encoding='utf-8') as f:
        f.write(content)


def load_snapshot(file_id: str) -> str:
    path = os.path.join(SNAPSHOTS_DIR, f"{file_id}.txt")
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    return ""


def save_analysis(file_id: str, analysis_data: dict) -> None:
    save_json_file(analysis_data, os.path.join(ANALYSIS_DIR, f"{file_id}.json"))


def log_previous_version(set_name: str, file_id: str, timestamp: str) -> None:
    log_timestamp = datetime.fromisoformat(timestamp).strftime('%Y%m%d_%H%M%S')
    old_analysis_path = os.path.join(ANALYSIS_DIR, f"{file_id}.json")
    if os.path.exists(old_analysis_path):
        shutil.copy(old_analysis_path, os.path.join(LOG_DIR, f"{set_name}_{log_timestamp}_analysis.json"))
    old_snapshot_path = os.path.join(SNAPSHOTS_DIR, f"{file_id}.txt")
    if os.path.exists(old_snapshot_path):
        shutil.copy(old_snapshot_path, os.path.join(LOG_DIR, f"{set_name}_{log_timestamp}_snapshot.txt"))


def validate_policy_sets(policy_sets: list) -> list:
    valid = []
    for i, ps in enumerate(policy_sets):
        if not isinstance(ps, dict):
            log.warning("Skipping policy_sets[%d]: not a dict", i)
            continue
        if not ps.get("setName"):
            log.warning("Skipping policy_sets[%d]: missing 'setName'", i)
            continue
        if not ps.get("category"):
            log.warning("Skipping policy_sets[%d] (%s): missing 'category'", i, ps.get("setName"))
            continue
        if not isinstance(ps.get("urls"), list) or len(ps["urls"]) == 0:
            log.warning("Skipping policy_sets[%d] (%s): missing or empty 'urls'", i, ps.get("setName"))
            continue
        valid.append(ps)
    return valid


# --- Content Fetching ---

def get_smarter_content_from_url(url_data: dict, driver, driver_type: str = "Direct") -> Optional[str]:
    url = url_data['url']
    selector = url_data.get('selector')

    try:
        log.info("    [%s] Navigating to %s", driver_type, url)
        driver.get(url)

        WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body'))
        )

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 4);")
        time.sleep(random.uniform(2, 4))
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
        time.sleep(random.uniform(1, 3))

        html = driver.page_source

        page_text_lower = html.lower()
        if any(sig.lower() in page_text_lower for sig in FAILURE_SIGNATURES):
            log.warning("    [%s] Block page or browser error detected", driver_type)
            return None

        soup = BeautifulSoup(html, 'html.parser')

        # Use the CSS selector from policy_sets.json if provided
        if selector:
            elements = soup.select(selector)
            if elements:
                for el in elements:
                    for tag in el.select('nav, footer, script, style, aside, .noprint, #sidebar'):
                        tag.decompose()
                return '\n'.join(el.get_text(separator='\n', strip=True) for el in elements)
            else:
                log.warning("    [%s] Selector '%s' matched no elements, falling back to body", driver_type, selector)

        # Fallback: use body with noise removal
        page_body = soup.body
        if page_body:
            tags_to_exclude = ['nav', 'footer', 'header', 'script', 'style', 'aside', '.noprint', '#sidebar']
            for tag_selector in tags_to_exclude:
                for tag in page_body.select(tag_selector):
                    tag.decompose()
            return page_body.get_text(separator='\n', strip=True)
        else:
            log.warning("    [%s] Page loaded but no body content found", driver_type)
            return ""

    except TimeoutException:
        log.error("    [%s] Timed out waiting for page body at %s", driver_type, url)
        return None
    except WebDriverException as e:
        log.error("    [%s] WebDriver error for %s: %s", driver_type, url, type(e).__name__)
        return None
    except Exception as e:
        log.error("    [%s] Unexpected error fetching %s: %s", driver_type, url, e)
        return None


def fetch_with_retry(url_data: dict, policy_set: dict) -> Optional[str]:
    for attempt in range(1, MAX_RETRIES + 1):
        content = None
        driver = None

        # Attempt direct connection
        if not policy_set.get("force_proxy"):
            try:
                log.info("  Attempt %d: direct connection for %s", attempt, url_data['url'])
                driver = initialize_driver(with_proxy=False)
                if driver:
                    content = get_smarter_content_from_url(url_data, driver, "Direct")
            finally:
                if driver:
                    driver.quit()

        # Attempt proxy connection if direct failed
        if content is None:
            driver = None
            try:
                log.info("  Attempt %d: proxy connection for %s", attempt, url_data['url'])
                driver = initialize_driver(with_proxy=True)
                if driver:
                    content = get_smarter_content_from_url(url_data, driver, "Proxy")
            finally:
                if driver:
                    driver.quit()

        if content is not None:
            return content

        if attempt < MAX_RETRIES:
            log.warning("  Retrying in %ds (attempt %d/%d)", RETRY_DELAY_SECONDS, attempt, MAX_RETRIES)
            time.sleep(RETRY_DELAY_SECONDS)

    return None


# --- AI Analysis ---

GEMINI_PROMPT_TEMPLATE = """You are an AI policy analyst specializing in Terms of Service and government policy changes.

Analyze the following changes to a policy document named "{set_name}".

Your response MUST be a valid JSON object with exactly four keys:

{{
  "summary": "A 1-2 sentence plain-language summary of what changed",
  "analysis": "A detailed markdown analysis covering: 1) What specifically changed, 2) Who is affected, 3) Whether this impacts user rights, data privacy, or liability, 4) Any action required",
  "date_time": "The current ISO 8601 timestamp",
  "priority": "One of: critical, high, medium, low"
}}

Priority definitions:
- **critical**: Changes that directly affect user rights, data handling, liability, or legal obligations
- **high**: Significant policy shifts that alter how a service operates or is governed
- **medium**: Notable but non-urgent changes such as clarifications or minor scope adjustments
- **low**: Cosmetic, formatting, or trivial wording changes with no practical impact

OLD CONTENT:
---
{old_content}
---

NEW CONTENT:
---
{new_content}
---"""


def get_gemini_analysis(set_name: str, old_content: str, new_content: str) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.error("GEMINI_API_KEY not set")
        return {
            "summary": "Analysis failed: API key not configured.",
            "analysis": "The Gemini API key was not provided as an environment variable.",
            "date_time": datetime.now(AEST_TZ).isoformat(),
            "priority": "critical",
        }

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)
    prompt = GEMINI_PROMPT_TEMPLATE.format(
        set_name=set_name,
        old_content=old_content[:50000],
        new_content=new_content[:50000],
    )

    try:
        response = model.generate_content(prompt)
        cleaned_text = re.sub(r'^```json\s*|\s*```$', '', response.text, flags=re.MULTILINE | re.DOTALL).strip()
        result = json.loads(cleaned_text)
        for key in ('summary', 'analysis', 'date_time', 'priority'):
            if key not in result:
                log.warning("Gemini response missing key '%s', adding default", key)
                result.setdefault(key, 'Unknown')
        return result
    except json.JSONDecodeError as e:
        log.error("Failed to parse Gemini response as JSON: %s", e)
        return {
            "summary": "Analysis failed: could not parse AI response.",
            "analysis": f"The AI returned a response that was not valid JSON. Raw text: {response.text[:500]}",
            "date_time": datetime.now(AEST_TZ).isoformat(),
            "priority": "medium",
        }
    except Exception as e:
        log.error("Gemini API error: %s", e)
        return {
            "summary": "Analysis failed.",
            "analysis": f"API error: {type(e).__name__}: {e}",
            "date_time": datetime.now(AEST_TZ).isoformat(),
            "priority": "medium",
        }


# --- WebDriver ---

def initialize_driver(with_proxy: bool = False) -> Optional[webdriver.Chrome]:
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
    chrome_options.add_argument('--lang=en-US,en;q=0.9')
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    if with_proxy:
        proxy_host, proxy_port, proxy_user, proxy_pass = (
            os.environ.get(k) for k in ["PROXY_HOST", "PROXY_PORT", "PROXY_USER", "PROXY_PASS"]
        )
        if all([proxy_host, proxy_port, proxy_user, proxy_pass]):
            chrome_options.add_argument(f'--proxy-server=http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}')
        else:
            log.warning("Proxy requested but credentials incomplete")
            return None

    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        stealth(driver,
                languages=["en-US", "en"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
                )
        return driver
    except Exception as e:
        log.error("Failed to initialize WebDriver: %s", e)
        return None


# --- Main Entry Point ---

def main() -> None:
    setup_directories()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        log.error("GEMINI_API_KEY environment variable is not set. Exiting.")
        sys.exit(1)

    policy_sets = load_json_file(POLICY_SETS_FILE)
    if not isinstance(policy_sets, list):
        log.error("policy_sets.json must contain a JSON array. Exiting.")
        sys.exit(1)

    policy_sets = validate_policy_sets(policy_sets)
    if not policy_sets:
        log.error("No valid policy sets found in %s. Exiting.", POLICY_SETS_FILE)
        sys.exit(1)

    previous_hashes = load_json_file(HASHES_FILE)
    current_hashes = {}

    for policy_set in policy_sets:
        set_name = policy_set["setName"]
        log.info("Processing Policy Set: %s", set_name)

        aggregated_content = []
        for url_data in policy_set["urls"]:
            content = fetch_with_retry(url_data, policy_set)
            if content:
                aggregated_content.append(f"--- Content from {url_data['url']} ---\n\n{content}")
            else:
                log.warning("  All attempts to fetch %s failed", url_data['url'])

        if not aggregated_content:
            log.warning("Skipping '%s': no content could be fetched from any URL", set_name)
            if set_name in previous_hashes:
                current_hashes[set_name] = previous_hashes[set_name]
            continue

        full_content = "\n\n".join(aggregated_content)
        new_hash = generate_md5(full_content)
        timestamp = datetime.now(AEST_TZ).isoformat()
        file_id = slugify_set_name(set_name)
        previous_entry = previous_hashes.get(set_name, {})
        previous_hash = previous_entry.get("hash")

        current_hashes[set_name] = {
            "hash": new_hash,
            "category": policy_set["category"],
            "urls": policy_set["urls"],
            "file_id": file_id,
            "last_checked": timestamp,
            "last_amended": previous_entry.get("last_amended"),
            "last_priority": previous_entry.get("last_priority"),
        }

        if not previous_hash:
            log.info("  First scan for '%s'", set_name)
            current_hashes[set_name]["last_amended"] = timestamp
            current_hashes[set_name]["last_priority"] = "low"
            save_snapshot(file_id, full_content)
            save_analysis(file_id, {
                "summary": "Initial snapshot captured.",
                "analysis": f"This is the first time the '{set_name}' policy set has been monitored.",
                "date_time": timestamp,
                "priority": "low",
            })
        elif new_hash != previous_hash:
            log.info("  Change detected for '%s'. Analyzing...", set_name)
            current_hashes[set_name]["last_amended"] = timestamp
            if previous_entry.get("last_checked"):
                log_previous_version(file_id, file_id, previous_entry["last_checked"])
            old_content = load_snapshot(file_id)
            analysis_result = get_gemini_analysis(set_name, old_content, full_content)
            current_hashes[set_name]["last_priority"] = analysis_result.get("priority", "medium")
            save_analysis(file_id, analysis_result)
            save_snapshot(file_id, full_content)
            log.info("  Analysis complete. Priority: %s", analysis_result.get('priority', 'N/A'))
        else:
            log.info("  No changes detected for '%s'", set_name)

    save_json_file(current_hashes, HASHES_FILE)
    log.info("Update check complete.")


if __name__ == "__main__":
    main()
