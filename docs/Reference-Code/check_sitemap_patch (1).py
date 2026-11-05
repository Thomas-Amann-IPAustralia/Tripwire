"""
INTEGRATION PATCH — check_sitemap.py
=====================================

This file shows the exact changes needed to wire url_reconciler.py into the
existing check_sitemap.py.  It is written as a unified diff-style walkthrough
with full replacement blocks so there is no ambiguity about where code goes.

ASSUMPTION: check_sitemap.py currently has a structure roughly like this:

    1.  Fetch sitemap XML via Selenium
    2.  Filter for /options/ URLs
    3.  Load metatable-Content.csv into a list of dicts
    4.  Find URLs in sitemap but NOT in CSV  →  "new_urls"
    5.  For each new_url: append a new row, auto-generate A-prefix UDID
    6.  Write updated CSV back to disk
    7.  Dispatch Stage 1 if any new rows were added

The patch adds a Phase 2.5 between steps 4 and 5:
    4.5  For each new_url, fetch its content and compare against existing
         .md files using MinHash.  Rename or new-page decision is made here.
"""

# ── STEP 1: Add to imports at the top of check_sitemap.py ────────────────────

from url_reconciler import (
    ExistingPageSignatures,
    reconcile_new_url,
    JACCARD_THRESHOLD,
)

# ── STEP 2: After loading CSV rows, build the signature store ─────────────────
#
# Place this block immediately after you've loaded `existing_rows` from the CSV
# and BEFORE the loop that processes new URLs.
#
# `MD_DIR` should match the IPFR-Webpages/ path used by the rest of the script.
# Typically this is already defined as a constant or Path near the top of the file.

MD_DIR = Path("IPFR-Webpages")   # adjust if the script uses a different variable name

existing_signatures = ExistingPageSignatures(md_dir=MD_DIR)
existing_signatures.load_from_csv_rows(existing_rows)


# ── STEP 3: Replace the "append new row" loop with the reconciled version ─────
#
# BEFORE (the old logic — delete this):
#
#   for new_url in new_urls:
#       new_udid = generate_next_a_udid(existing_rows)
#       slug = url_to_slug(new_url)
#       existing_rows.append({
#           "UDID": new_udid,
#           "Main-title": slug,
#           "Canonical-url": new_url,
#           ... other columns with empty defaults ...
#       })
#       changes_made = True
#
# AFTER (replace with this):

changes_made = False
rename_log: list[dict] = []    # kept for the summary report at the end
new_page_log: list[dict] = []

for new_url in new_urls:
    # ── 3a. Fetch the page content (Markdown) for similarity comparison ───────
    #
    # Re-use whatever fetch/scrape helper already exists in check_sitemap.py.
    # If there isn't one, call scraper logic or a lightweight requests fetch here.
    # An empty string is safe — reconcile_new_url will classify it as a new page.
    try:
        new_page_text = fetch_page_as_markdown(new_url)   # your existing helper
    except Exception as exc:
        logger.warning("Could not fetch %s for similarity check: %s", new_url, exc)
        new_page_text = ""

    # ── 3b. Reconcile ─────────────────────────────────────────────────────────
    decision = reconcile_new_url(new_url, new_page_text, existing_signatures)

    if decision["verdict"] == "rename":
        # ── 3c. URL rename: update the existing row in-place ──────────────────
        #
        # Find the matching row by UDID and update ONLY Canonical-url.
        # All other metadata (UDID, Main-title, Archetype, etc.) is preserved.
        matched_udid = decision["udid"]
        for row in existing_rows:
            if row["UDID"] == matched_udid:
                old_url = row["Canonical-url"]
                row["Canonical-url"] = new_url
                # Stamp a note so humans can audit the change history.
                # If your CSV doesn't have a Notes column, consider adding one.
                row["Notes"] = (
                    row.get("Notes", "").rstrip("; ")
                    + f"; URL updated from {old_url} (Jaccard={decision['jaccard']:.3f})"
                ).lstrip("; ")
                logger.info(
                    "Updated Canonical-url for %s: %s → %s",
                    matched_udid, old_url, new_url,
                )
                rename_log.append(decision)
                changes_made = True
                break

    else:
        # ── 3d. Genuinely new page: append as before ──────────────────────────
        new_udid = generate_next_a_udid(existing_rows)   # your existing helper
        slug = url_to_slug(new_url)                       # your existing helper
        existing_rows.append({
            "UDID": new_udid,
            "Main-title": slug,
            "Canonical-url": new_url,
            # Keep all other columns at their empty/default values
            "Archetype": "",
            "Relevant-ip-right": "",
            "Provider": "",
            "Overtitle": "",
            "Description": "",
            "Last-updated": "",
            "Notes": "",
        })
        logger.info("New page appended: %s → %s", new_udid, new_url)
        new_page_log.append(decision)
        changes_made = True


# ── STEP 4: Extend the summary / dispatch block ───────────────────────────────
#
# Wherever check_sitemap.py currently prints a summary or logs what happened,
# add the rename breakdown:

if changes_made:
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
        logger.info("New pages added: %d", len(new_page_log))
        for entry in new_page_log:
            logger.info("  NEW     %s", entry["new_url"])

    # Dispatch Stage 1 as before — renames also need a re-scrape since the
    # URL has changed and scraper.py targets Canonical-url.
    dispatch_stage_1()   # your existing dispatch call


# ── NOTES ─────────────────────────────────────────────────────────────────────
#
# Q: What if an existing page's .md file doesn't exist yet?
# A: ExistingPageSignatures silently skips it.  That row won't participate in
#    rename matching.  Worst case: a rename is misclassified as a new page and
#    gets a new A-prefix UDID.  This is recoverable manually and is far better
#    than silently deleting metadata.
#
# Q: What if the threshold is wrong for a specific page?
# A: Check the Jaccard score in the Notes column after a run.  Adjust
#    JACCARD_THRESHOLD in url_reconciler.py if you find systematic
#    false positives (renames treated as new) or false negatives (new pages
#    treated as renames).  0.5 is conservative; government page rewrites
#    typically score 0.65–0.85.
#
# Q: Does this add significant runtime?
# A: MinHash construction for 200 .md files at 128 permutations is ~0.5 sec
#    total.  The per-new-URL comparison is O(n) over existing signatures —
#    negligible for a 200-row CSV.
#
# Q: What about the Notes column not existing in the CSV?
# A: Use row.get("Notes", "") defensively (already done above).  If the column
#    is absent entirely, csv.DictWriter will silently omit it — add "Notes" to
#    your fieldnames list when writing the CSV back to disk.
