"""
Sitemap Monitor for IP First Response.

Fetches the IPFR sitemap via Selenium stealth (site is behind a WAF),
extracts /options/ URLs, compares against metatable-Content.csv,
and appends new entries for any discovered pages.

Also detects pages already in the CSV whose sitemap <lastmod> date is
newer than the recorded Last-updated value, and flags them for re-scraping.

Detects and removes CSV entries whose URLs no longer appear in the sitemap,
logging each deletion loudly so they are easy to spot in the action log.

Can be run manually or on a weekly schedule via GitHub Actions.
"""

import csv
import os
import sys
import re
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium_stealth import stealth

from url_reconciler import (
    ExistingPageSignatures,
    reconcile_new_url,
    JACCARD_THRESHOLD,
)

# --- Configuration ---
SITEMAP_URL = 'https://ipfirstresponse.ipaustralia.gov.au/sitemap.xml'
CSV_FILE = 'metatable-Content.csv'
OPTIONS_PATH_PREFIX = '/options/'
UDID_PREFIX = 'A'
UDID_START = 1000
MD_DIR = Path('IPFR-Webpages')

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("SitemapMonitor")


def initialize_driver():
    """Sets up a stealthy Headless Chrome driver (same config as scraper)."""
    logger.info("Initializing Selenium Driver...")
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    )

    try:
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        stealth(driver,
                languages=["en-US", "en"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True)
        return driver
    except Exception as e:
        logger.error(f"Failed to initialize WebDriver: {e}")
        return None


def fetch_sitemap(driver, url):
    """Fetches sitemap XML via the browser's XHR (bypasses Chrome's XML viewer)
    and returns a dict mapping url -> lastmod string (or None if absent).

    Handles sitemap index files by recursively fetching sub-sitemaps.
    """
    logger.info(f"Fetching sitemap: {url}")

    # Navigate first so cookies / WAF session tokens are established,
    # then use a synchronous XHR inside the same browser context to retrieve
    # the raw XML text before Chrome has a chance to render it into its
    # shadow-DOM XML viewer (which hides <loc> tags from page_source).
    driver.get(url)
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.TAG_NAME, 'body'))
    )

    xml_text = driver.execute_script("""
        var xhr = new XMLHttpRequest();
        xhr.open('GET', arguments[0], false);
        xhr.send(null);
        return xhr.responseText;
    """, url)

    url_map = {}  # url -> lastmod (str or None)

    try:
        # Strip default XML namespace so ElementTree findall paths stay simple
        xml_clean = re.sub(r'\sxmlns(?::[^=]+)?="[^"]+"', '', xml_text)
        root = ElementTree.fromstring(xml_clean)

        # Sitemap index — recurse into each sub-sitemap
        sitemapindex_entries = root.findall('.//sitemap/loc')
        if sitemapindex_entries:
            logger.info(f"Found sitemap index with {len(sitemapindex_entries)} sub-sitemaps")
            for loc_el in sitemapindex_entries:
                sub_map = fetch_sitemap(driver, loc_el.text.strip())
                url_map.update(sub_map)
        else:
            # Regular sitemap — collect loc + optional lastmod per <url> block
            for url_el in root.findall('.//url'):
                loc_el = url_el.find('loc')
                if loc_el is None or not loc_el.text:
                    continue
                loc = loc_el.text.strip()
                lastmod_el = url_el.find('lastmod')
                lastmod = lastmod_el.text.strip() if lastmod_el is not None and lastmod_el.text else None
                url_map[loc] = lastmod

    except ElementTree.ParseError:
        logger.warning("XML parsing failed, falling back to regex extraction")
        # Capture loc and the immediately following lastmod (if any)
        for loc, lastmod in re.findall(
            r'<loc>\s*(https?://[^<]+)\s*</loc>'
            r'(?:\s*<lastmod>\s*([^<]*?)\s*</lastmod>)?',
            xml_text
        ):
            url_map[loc.strip()] = lastmod.strip() if lastmod else None

    logger.info(f"Extracted {len(url_map)} total URLs from sitemap")
    return url_map


def filter_options_urls(url_map):
    """Filter url_map to only include /options/ paths."""
    return {
        url: lastmod
        for url, lastmod in url_map.items()
        if OPTIONS_PATH_PREFIX in urlparse(url).path
    }


def parse_sitemap_date(date_str):
    """Parse an ISO 8601 lastmod string to a date object, or None."""
    if not date_str:
        return None
    s = date_str.strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(s[:len(fmt)], fmt)
            return dt.date()
        except ValueError:
            continue
    logger.debug(f"Could not parse sitemap date: {date_str!r}")
    return None


def parse_csv_date(date_str):
    """Parse a DD/MM/YYYY Last-updated string to a date object, or None."""
    if not date_str:
        return None
    s = date_str.strip()
    for fmt in ('%d/%m/%Y', '%-d/%-m/%Y', '%d/%m/%Y', '%d/%m/%y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    logger.debug(f"Could not parse CSV date: {date_str!r}")
    return None


def read_existing_csv(csv_path):
    """Read existing CSV and return rows, fieldnames, set of existing URLs,
    and a dict mapping url -> Last-updated string."""
    rows = []
    fieldnames = []
    existing_urls = set()
    url_last_updated = {}

    if not os.path.exists(csv_path):
        logger.error(f"CSV file not found: {csv_path}")
        return rows, fieldnames, existing_urls, url_last_updated

    with open(csv_path, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)
            url = row.get('Canonical-url', '').strip()
            if url:
                existing_urls.add(url)
                url_last_updated[url] = row.get('Last-updated', '').strip()

    logger.info(f"Read {len(rows)} existing entries from CSV")
    return rows, fieldnames, existing_urls, url_last_updated


def get_next_udid(rows):
    """Find the next available A-prefix UDID number."""
    max_num = UDID_START - 1
    pattern = re.compile(rf'^{UDID_PREFIX}(\d+)$')

    for row in rows:
        udid = row.get('UDID', '').strip()
        match = pattern.match(udid)
        if match:
            num = int(match.group(1))
            if num > max_num:
                max_num = num

    return max_num + 1


def title_from_slug(url):
    """Derive a human-readable title from a URL slug.

    Example: /options/register-your-trade-mark -> Register your trade mark
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    slug = path.split('/')[-1]
    title = slug.replace('-', ' ').strip()
    if title:
        title = title[0].upper() + title[1:]
    return title


def fetch_page_text(driver, url):
    """Fetch the main text of a page using the stealth driver.

    Used by the MinHash reconciliation step to compare a newly-discovered URL
    against existing .md files.  Mirrors the selector priority in scraper.py:
    tries <main>, then .region-content, then falls back to <body>.

    Returns an empty string on any error so reconcile_new_url() safely
    classifies the URL as a new page rather than crashing the run.
    """
    try:
        driver.get(url)
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body'))
        )
        for selector in ('main', '.region-content'):
            try:
                el = driver.find_element(By.CSS_SELECTOR, selector)
                text = el.text.strip()
                if text:
                    return text
            except Exception:
                continue
        return driver.find_element(By.TAG_NAME, 'body').text.strip()
    except Exception as exc:
        logger.warning("fetch_page_text failed for %s: %s", url, exc)
        return ""


def append_new_urls(csv_path, fieldnames, existing_rows, new_urls, next_udid_num):
    """Append new URL entries to the CSV file without modifying existing rows."""
    new_rows = []

    for i, url in enumerate(sorted(new_urls)):
        udid = f"{UDID_PREFIX}{next_udid_num + i}"
        title = title_from_slug(url)

        new_row = {field: '' for field in fieldnames}
        new_row['UDID'] = udid
        new_row['Main-title'] = title
        new_row['Canonical-url'] = url

        new_rows.append(new_row)
        logger.info(f"  New entry: {udid} - {title} ({url})")

    all_rows = existing_rows + new_rows

    with open(csv_path, mode='w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    logger.info(f"Appended {len(new_rows)} new entries to {csv_path}")
    return new_rows


def stamp_last_updated(rows, updated_urls):
    """Update the Last-updated column in memory for pages whose sitemap lastmod
    is newer than the recorded value.

    Converts the ISO 8601 lastmod date to DD/MM/YYYY and writes it into each
    matching row dict.  Does not write to disk — the caller is responsible for
    persisting the changes (either via a subsequent append_new_urls call or by
    writing the rows directly).

    Returns the number of rows modified.
    """
    count = 0
    for row in rows:
        url = row.get('Canonical-url', '').strip()
        if url in updated_urls:
            new_date = parse_sitemap_date(updated_urls[url])
            if new_date:
                row['Last-updated'] = new_date.strftime('%d/%m/%Y')
                count += 1
                logger.info(f"  Stamped Last-updated: {url} -> {row['Last-updated']}")
    return count


def remove_deleted_urls(csv_path, fieldnames, existing_rows, deleted_urls):
    """Remove rows for URLs no longer present in the sitemap.

    Logs each deletion loudly at WARNING/ERROR level so they are impossible
    to miss in the GitHub Actions log, then rewrites the CSV without them.
    Returns the number of rows removed.
    """
    sep = "!" * 70
    logger.warning(sep)
    logger.warning(
        f"!!! SITEMAP DELETION ALERT: "
        f"{len(deleted_urls)} URL(s) no longer appear in the sitemap !!!"
    )
    logger.warning(sep)
    for url in sorted(deleted_urls):
        logger.error(f"  [DELETED FROM CSV] {url}")
    logger.warning(sep)

    kept_rows = [
        row for row in existing_rows
        if row.get('Canonical-url', '').strip() not in deleted_urls
    ]
    removed_count = len(existing_rows) - len(kept_rows)

    with open(csv_path, mode='w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept_rows)

    logger.warning(sep)
    logger.error(
        f"!!! {removed_count} row(s) permanently deleted from {csv_path} !!!"
    )
    logger.warning(sep)
    return removed_count


def set_github_output(name, value):
    """Set a GitHub Actions output variable."""
    output_file = os.environ.get('GITHUB_OUTPUT')
    if output_file:
        with open(output_file, 'a') as f:
            f.write(f"{name}={value}\n")
    else:
        logger.info(f"[Output] {name}={value}")


def main():
    # Read existing CSV
    rows, fieldnames, existing_urls, url_last_updated = read_existing_csv(CSV_FILE)
    if not fieldnames:
        logger.critical(f"Could not read CSV headers from {CSV_FILE}")
        sys.exit(1)

    # Ensure Notes column exists for rename audit trail (not in original schema)
    if 'Notes' not in fieldnames:
        fieldnames = list(fieldnames) + ['Notes']
        for row in rows:
            row.setdefault('Notes', '')

    # Build MinHash signatures from existing .md files before starting the browser.
    # Rows without a corresponding .md file are silently skipped (safe fallback).
    existing_signatures = ExistingPageSignatures(md_dir=MD_DIR)
    existing_signatures.load_from_csv_rows(rows)

    # Initialize browser
    driver = initialize_driver()
    if not driver:
        logger.critical("Failed to initialize browser")
        sys.exit(1)

    try:
        # Fetch and parse sitemap (returns url -> lastmod dict)
        all_url_map = fetch_sitemap(driver, SITEMAP_URL)
        options_url_map = filter_options_urls(all_url_map)
        logger.info(f"Found {len(options_url_map)} /options/ URLs in sitemap")

        # --- Detect new pages (not yet in CSV) ---
        new_urls = {url: lm for url, lm in options_url_map.items()
                    if url not in existing_urls}
        logger.info(f"New URLs not in CSV: {len(new_urls)}")

        # --- Detect updated pages (in CSV, but sitemap lastmod is newer) ---
        updated_urls = {}
        for url, lastmod in options_url_map.items():
            if url not in existing_urls:
                continue  # handled above as new
            sitemap_date = parse_sitemap_date(lastmod)
            csv_date = parse_csv_date(url_last_updated.get(url, ''))
            if sitemap_date and csv_date and sitemap_date > csv_date:
                updated_urls[url] = lastmod
                logger.info(
                    f"  Updated page detected: {url}"
                    f" (sitemap: {sitemap_date}, csv: {csv_date})"
                )

        logger.info(f"Updated URLs (sitemap newer than CSV): {len(updated_urls)}")

        # --- Detect deleted pages (in CSV but absent from sitemap) ---
        # Only compare /options/ URLs so non-options CSV rows are never flagged.
        existing_options_urls = {
            url for url in existing_urls
            if OPTIONS_PATH_PREFIX in urlparse(url).path
        }
        deleted_urls = existing_options_urls - set(options_url_map.keys())
        logger.info(f"URLs in CSV but absent from sitemap: {len(deleted_urls)}")

        # --- Stamp Last-updated for updated pages (in-memory, before any write) ---
        if updated_urls:
            stamp_count = stamp_last_updated(rows, updated_urls)
            logger.info(f"Stamped Last-updated for {stamp_count} row(s)")

        # --- Reconcile new URLs: URL rename vs genuinely new page ---
        # For each URL in the sitemap that isn't in the CSV, fetch its content
        # and compare against existing .md files via MinHash Jaccard similarity.
        # Renames update the existing row's Canonical-url in-place (UDID preserved).
        # Genuinely new pages get a fresh A-prefix UDID as before.
        rename_log: list[dict] = []
        new_page_log: list[dict] = []

        for url in sorted(new_urls.keys()):  # sorted for deterministic UDID assignment
            try:
                page_text = fetch_page_text(driver, url)
            except Exception as exc:
                logger.warning("Could not fetch %s for similarity check: %s", url, exc)
                page_text = ""

            decision = reconcile_new_url(url, page_text, existing_signatures)

            if decision["verdict"] == "rename":
                matched_udid = decision["udid"]
                for row in rows:
                    if row["UDID"] == matched_udid:
                        old_url = row["Canonical-url"]
                        row["Canonical-url"] = url
                        row["Notes"] = (
                            row.get("Notes", "").rstrip("; ")
                            + f"; URL updated from {old_url} (Jaccard={decision['jaccard']:.3f})"
                        ).lstrip("; ")
                        logger.info(
                            "Updated Canonical-url for %s: %s → %s",
                            matched_udid, old_url, url,
                        )
                        rename_log.append(decision)
                        break
            else:
                new_page_log.append(decision)

        if rename_log:
            logger.info(
                "URL renames detected: %d  (Jaccard threshold: %.2f)",
                len(rename_log), JACCARD_THRESHOLD,
            )
            for entry in rename_log:
                logger.info(
                    "  RENAME  UDID=%-8s  Jaccard=%.3f  %s  →  %s",
                    entry["udid"], entry["jaccard"], entry["old_url"], entry["new_url"],
                )
        if new_page_log:
            logger.info("New pages to add: %d", len(new_page_log))
            for entry in new_page_log:
                logger.info("  NEW     %s", entry["new_url"])

        truly_new_urls = [d["new_url"] for d in new_page_log]

        # --- Act on new/renamed pages ---
        # append_new_urls rewrites the whole CSV, so it will persist renamed
        # Canonical-urls and Last-updated stamps from the steps above at the same time.
        if truly_new_urls:
            next_num = get_next_udid(rows)
            append_new_urls(CSV_FILE, fieldnames, rows, truly_new_urls, next_num)
            logger.info(f"SUCCESS: Added {len(truly_new_urls)} new URL(s) to {CSV_FILE}")
            if rename_log:
                logger.info(f"Also persisted {len(rename_log)} URL rename(s)")
        elif rename_log or updated_urls:
            # No new rows to append, but renames/Last-updated were modified in memory —
            # write the modified rows to disk now.
            with open(CSV_FILE, mode='w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            if rename_log:
                logger.info(f"Wrote back {len(rename_log)} URL rename(s) to {CSV_FILE}")
            if updated_urls:
                logger.info(f"Wrote back Last-updated stamps for {len(updated_urls)} row(s)")

        # --- Act on deleted pages ---
        if deleted_urls:
            # Re-read rows so we work on the post-append/stamp state when
            # multiple change types occur in the same run.
            current_rows, _, _, _ = read_existing_csv(CSV_FILE)
            # Notes may not yet be in re-read rows if this is the first run
            # adding the column; initialise to avoid DictWriter KeyError.
            for row in current_rows:
                row.setdefault('Notes', '')
            remove_deleted_urls(CSV_FILE, fieldnames, current_rows, deleted_urls)

        # --- Set GitHub Actions outputs ---
        needs_rescrape = bool(truly_new_urls or updated_urls or rename_log)
        csv_changed = bool(truly_new_urls or updated_urls or deleted_urls or rename_log)

        set_github_output('new_urls_found',      'true' if truly_new_urls else 'false')
        set_github_output('new_url_count',       str(len(truly_new_urls)))
        set_github_output('renamed_urls_found',  'true' if rename_log else 'false')
        set_github_output('renamed_url_count',   str(len(rename_log)))
        set_github_output('updated_urls_found',  'true' if updated_urls else 'false')
        set_github_output('updated_url_count',   str(len(updated_urls)))
        set_github_output('deleted_urls_found',  'true' if deleted_urls else 'false')
        set_github_output('deleted_url_count',   str(len(deleted_urls)))
        set_github_output('needs_rescrape',      'true' if needs_rescrape else 'false')
        set_github_output('csv_changed',         'true' if csv_changed else 'false')

        if not (needs_rescrape or deleted_urls):
            logger.info("No new, updated, renamed, or deleted URLs found - CSV is up to date")

    except Exception as e:
        logger.error(f"Error during sitemap check: {e}")
        set_github_output('new_urls_found',      'false')
        set_github_output('new_url_count',       '0')
        set_github_output('renamed_urls_found',  'false')
        set_github_output('renamed_url_count',   '0')
        set_github_output('updated_urls_found',  'false')
        set_github_output('updated_url_count',   '0')
        set_github_output('deleted_urls_found',  'false')
        set_github_output('deleted_url_count',   '0')
        set_github_output('needs_rescrape',      'false')
        set_github_output('csv_changed',         'false')
        sys.exit(1)
    finally:
        driver.quit()
        logger.info("--- Sitemap Check Complete ---")


if __name__ == "__main__":
    main()
