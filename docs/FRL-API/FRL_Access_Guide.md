# Accessing the Federal Register of Legislation (FRL)

A self-contained guide to fetching Australian legislation from
`legislation.gov.au` — the full text of a document, and the signals that tell
you when it has been amended.

Written for an agent building an **offline snapshot** of a whole instrument.
Everything here is derived from the working implementation in this repo
(`src/stage1_metadata.py`, `src/stage3_diff.py`) and was verified live against
the production API on 2026-07-29.

---

## 0. TL;DR

| Need | Do this |
|---|---|
| Full text of a document | `GET /v1/documents/find(titleid='<id>',asatspecification='Latest',type='Primary',format='Word',uniqueTypeNumber=0,volumeNumber=0,rectificationVersionNumber=0)` → binary `.docx` |
| "Has it been amended?" | `GET /v1/Versions/Find(titleId='<id>',asAtSpecification='Latest')` → compare `registerId` |
| "What changed and why?" | Same call, read the `reasons[]` array |
| Plain-English explanation of the change | Amending instrument's Explanatory Statement (`type='ES'`) or, for Acts, the bill materials on ParlInfo |

**Do not scrape the website for document text.** See §5.

---

## 1. The two identifier systems

Get these right and everything else follows.

- **`titleId`** — identifies the *law*, permanently. `C2004A04969` is the Trade
  Marks Act 1995 forever, across every amendment. `C…A…` = Act,
  `F…L…` / `F…B…` = legislative instrument (regulations, rules).
- **`registerId`** — identifies one *compiled version* of that law.
  `C2024C00545` is Compilation No. 47 of the Trade Marks Act, in force from
  14 October 2024. **A new `registerId` is the amendment signal.**

Also useful: `compilationNumber` (`'0'` = as-made, then `'1'`, `'2'`, …).

The `titleId` is the first path segment of any public URL:

```
https://www.legislation.gov.au/C2004A04969/latest/text
                               ^^^^^^^^^^^ titleId
```

Extraction (from `src/stage1_metadata.py`):

```python
import re
from urllib.parse import urlparse

_FRL_TITLE_ID_RE = re.compile(r"^[A-Z]\d{4}[A-Z]\w+$")

def extract_frl_title_id(url: str) -> str | None:
    """C2004A04969, F1996B00084, F2024L01179, C2025Q00003 …"""
    segments = [s for s in urlparse(url).path.split("/") if s]
    if not segments:
        return None
    if segments[0].lower() == "series" and len(segments) >= 2:
        return segments[1]           # legacy /Series/<titleId> form
    for seg in segments:
        if _FRL_TITLE_ID_RE.match(seg):
            return seg
    return segments[0]
```

---

## 2. The API

```
Base URL:  https://api.prod.legislation.gov.au
Auth:      none for public reads
Protocol:  OData v4 ($filter, $select, $expand, $orderby, $top, $skip, $count)
```

An OpenAPI spec is checked in at `docs/FRL-API/FRL_Instructions.json`
(~17 000 lines) with a navigation index at `FRL_API_Index.json`. Read the index
first — every endpoint appears 2–3 times in the spec under different casing
conventions, and 123 of the 153 schemas are ASP.NET framework noise.

Entity sets: `Titles`, `Versions`, `Documents`, `Content`, `Affect`,
`Departments`, `TextApplies`, plus `_*Search` contexts.

### 2.1 Function-call syntax — the part that bites

The `find()` endpoints are OData *functions*, so parameters go **inside
parentheses**, not in a query string, and string values need single quotes:

```
/v1/Versions/Find(titleId='C2004A04969',asAtSpecification='Latest')
```

Do **not** URL-encode the parens, commas, or quotes. If you use OData query
options elsewhere, encode the filter but preserve the literal `$` and quotes:

```python
from urllib.parse import quote
q = f"$filter={quote(f\"affectedTitleId eq '{title_id}'\", safe=chr(39))}&$top=50"
```

`urlencode()` will mangle this. Use `quote(..., safe="'")`.

### 2.2 Casing is inconsistent between endpoints

This is not a mistake in the examples below — it mirrors the live API:

- `Versions/Find(titleId=…, asAtSpecification=…)` — **camelCase**
- `documents/find(titleid=…, asatspecification=…)` — **all lowercase**

Both spellings are what the working code uses. Copy them exactly.

---

## 3. Getting the full document

### 3.1 The gotcha that costs hours

`documents/find()` declares `uniqueTypeNumber`, `volumeNumber` and
`rectificationVersionNumber` as optional with default `0`. **They are not
optional.** Omit them and you get a bare `404` with an empty body — identical
to the response for a document that genuinely does not exist, which makes it
look like the document is missing rather than the call being malformed.

Verified live, same document, same everything else:

```
find(titleid='F2025L01380',asatspecification='AsMade',type='ES',format='Word')
  → HTTP 404, 0 bytes

find(titleid='F2025L01380',asatspecification='AsMade',type='ES',format='Word',
     uniqueTypeNumber=0,volumeNumber=0,rectificationVersionNumber=0)
  → HTTP 200, 81 782 bytes, application/vnd.openxmlformats-…wordprocessingml.document
```

Always pass all three. (This repo's own `_fetch_regulation_explainer` in
`src/stage3_diff.py` omits them, which is why it 404s so often and leans on the
website fallback in §3.4.)

### 3.2 The call

```python
import requests

API = "https://api.prod.legislation.gov.au"

def download_document(
    title_id: str,
    *,
    doc_type: str = "Primary",       # Primary | ES | SupplementaryES
                                     # | SupportingMaterial | IncorporatedByReference
    fmt: str = "Word",               # Word | Pdf | Epub | NameOnly
    as_at: str = "Latest",           # Latest | AsMade | InForce
    session: requests.Session | None = None,
) -> bytes | None:
    """Download a document. Returns raw bytes, or None if not found."""
    s = session or requests.Session()
    url = (
        f"{API}/v1/documents/find("
        f"titleid='{title_id}',"
        f"asatspecification='{as_at}',"
        f"type='{doc_type}',"
        f"format='{fmt}',"
        f"uniqueTypeNumber=0,"          # ← required despite the spec
        f"volumeNumber=0,"
        f"rectificationVersionNumber=0)"
    )
    resp = s.get(url, timeout=120, allow_redirects=True)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    # A 200 carrying JSON is metadata, not a file — treat as a miss.
    if "json" in (resp.headers.get("Content-Type") or "").lower():
        return None
    return resp.content
```

Two response modes on the same URL:

- default → **binary file bytes**
- `Accept: application/json` → **document metadata**, no file

So an `Accept: application/json` header set globally on your session will
silently give you metadata where you wanted the document. Don't set it on the
document calls.

You can also address a specific historical compilation:

```
find(registerId='C2024C00545',type='Primary',format='Word',
     uniqueTypeNumber=0,volumeNumber=0,rectificationVersionNumber=0)
```

`compilationNumber='47'` and `asat=2024-10-14` variants exist in the spec but
returned 404 in live testing. Prefer `asatspecification` or `registerId`.

### 3.3 Which format for an offline snapshot

Live figures for the Trade Marks Act 1995, Compilation 47:

| Format | Size | Notes |
|---|---|---|
| `Word` | 261 KB | **Best for text extraction.** Clean paragraph structure. 348 772 chars extracted. |
| `Pdf` | 910 KB | The only `isAuthorised: true` rendition — use if legal authority matters. Bad for extraction. |
| `Epub` | 197 KB | HTML inside a zip; usable, structurally noisier. |

Take `Word`, and keep the `Pdf` alongside it if you need the authorised copy.

Extract via Mammoth → trafilatura (the pipeline's approach,
`src/scraper.py:extract_plain_text_from_docx`):

```python
import io, mammoth, trafilatura

def docx_to_text(docx_bytes: bytes) -> str:
    html = mammoth.convert_to_html(io.BytesIO(docx_bytes)).value
    return trafilatura.extract(html, include_tables=True, no_fallback=False) or ""
```

Dependency-free alternative if you can't install Mammoth:

```python
import zipfile, io, re, html as _html

def docx_to_text_stdlib(docx_bytes: bytes) -> str:
    xml = zipfile.ZipFile(io.BytesIO(docx_bytes)).read("word/document.xml").decode("utf-8")
    xml = xml.replace("</w:p>", "\n")
    return _html.unescape(re.sub(r"<[^>]+>", "", xml))
```

Note the extracted text contains raw Word field codes in the header
(`DOCPROPERTY CompilationNumber 47`). Strip or ignore the first ~10 lines.

### 3.4 Website fallback

Every document the API serves is also on the public site at a predictable path,
and this works when `documents/find()` doesn't (some instruments' documents are
lodged as direct files that never make it into the API's document index):

```
https://www.legislation.gov.au/{titleId}/latest/{startDate}/text/original/word
https://www.legislation.gov.au/{titleId}/asmade/{startDate}/es/original/word
```

`{startDate}` is `YYYY-MM-DD`, taken from the `start` field of the relevant
Version (`Versions/Find(titleId=…, asAtSpecification='AsMade')` for the
as-made date). Verified: the `/latest/…/text/original/word` URL returns byte-for-byte
the same 261 317-byte docx as the API.

Guard against HTML error pages served as `200`:

```python
if "html" in (resp.headers.get("Content-Type") or "").lower() and len(resp.content) < 50_000:
    ...  # error page masquerading as success
```

### 3.5 Enumerating what documents exist

Before downloading, you can list every rendition of a compilation:

```
GET /v1/Documents?$filter=registerId eq 'C2024C00545'
Accept: application/json
```

Returns `type`, `format`, `extension`, `sizeInBytes`, `isAuthorised` per file.
`$filter=titleId eq '…'` gives the entire document history of the law, right
back to `.rtf` files from the 1990s. Use `sizeInBytes` to sanity-check your
download completed.

---

## 4. Detecting amendments

### 4.1 The primary signal

```
GET /v1/Versions/Find(titleId='C2004A04969',asAtSpecification='Latest')
Accept: application/json
```

Live response (trimmed):

```json
{
  "titleId": "C2004A04969",
  "registerId": "C2024C00545",
  "compilationNumber": "47",
  "start": "2024-10-14T00:00:00",
  "end": null,
  "isLatest": true,
  "isCurrent": true,
  "status": "InForce",
  "hasUnincorporatedAmendments": false,
  "registeredAt": "2024-10-14T13:40:38.9539588",
  "reasons": [ … ]
}
```

**Store `registerId`. Compare it on each check. If it changed, the law was
amended and your snapshot is stale.** That is the whole mechanism — this is
what `_probe_frl` in `src/stage1_metadata.py` does. It is far better than
hashing page content: it is stable, semantically meaningful, cheap (one small
JSON GET), and changes if and only if a new compilation was registered.

Store all three of `registerId`, `compilationNumber`, `start`.

```python
def frl_version(title_id: str, session) -> dict:
    url = (f"{API}/v1/Versions/Find("
           f"titleId='{title_id}',asAtSpecification='Latest')")
    r = session.get(url, headers={"Accept": "application/json"}, timeout=20)
    r.raise_for_status()
    data = r.json()          # Find() returns a single object, not an OData list
    if not isinstance(data, dict) or not data:
        raise ValueError(f"No latest version for {title_id}")
    return data

def has_changed(title_id: str, stored_register_id: str | None, session) -> bool:
    if stored_register_id is None:
        return True                       # no baseline — fetch
    return frl_version(title_id, session)["registerId"] != stored_register_id
```

Note `Find()` returns a **single object**, not an OData `{"value": [...]}`
envelope. Don't index into `["value"]`.

### 4.2 `hasUnincorporatedAmendments` — don't miss this

A `false` here means the compilation you're holding reflects all amendments in
force. **`true` means amendments have been made and commenced but are not yet
incorporated into any compilation** — the document you download is legally
out of date and the register knows it. `registerId` will not have changed.

For an offline snapshot this matters: check the flag on every poll and record
it alongside the text, so a downstream reader knows the snapshot is behind.

### 4.3 What changed — the `reasons` array

The same `Find()` response carries `reasons[]`, which names the amending
instrument. No `$expand` needed (and the equivalent list-endpoint query with
`$expand=Reasons` returns HTTP 400):

```json
"reasons": [{
  "affect": "Amend",
  "markdown": "sch 11 (items 80-88) of the [Administrative Review Tribunal (Consequential and Transitional Provisions No. 2) Act 2024](/C2024A00039)",
  "affectedByTitle": {
    "titleId": "C2024A00039",
    "name": "Administrative Review Tribunal (Consequential and Transitional Provisions No. 2) Act 2024",
    "provisions": "sch 11 (items 80-88)",
    "year": 2024, "number": 39,
    "seriesType": "Act"
  },
  "amendedByTitle": null,
  "dateChanged": null
}]
```

`provisions` tells you *which parts* changed — enough to target a re-read
rather than re-snapshot the whole document.

Extraction is defensive because the structured fields are unreliable:

```python
_ACT_ID_RE = re.compile(r"\bC\d{4}A\d+\b")

def extract_amending_instruments(version: dict) -> list[dict]:
    """Return [{'title_id': …, 'series_type': …}] for the amending instruments."""
    out, seen = [], set()

    def add(tid, stype=""):
        if tid and tid not in seen:
            seen.add(tid)
            out.append({"title_id": tid, "series_type": stype})

    # Layer 1: sometimes registerId *is* the amending Act's titleId.
    reg = version.get("registerId") or ""
    if re.match(r"^C\d{4}A\d+$", reg):
        add(reg, "Act")

    # Layer 2: the reasons array.
    for reason in version.get("reasons", []):
        if reason.get("affect") != "Amend":
            continue
        # Check BOTH — either can be null while the other holds the id.
        for field in ("affectedByTitle", "amendedByTitle"):
            ref = reason.get(field) or {}
            add(ref.get("titleId"), ref.get("seriesType") or "")
        # Last resort: scrape the id out of the markdown blob.
        if not (reason.get("affectedByTitle") or reason.get("amendedByTitle")):
            m = _ACT_ID_RE.search(reason.get("markdown", ""))
            if m:
                add(m.group(0), "")
    return out
```

`seriesType` is nullable even when populated elsewhere. When it's missing,
resolve it authoritatively rather than guessing:

```
GET /v1/Titles('C2024A00039')   →  {"seriesType": "Act", "originatingBillUri": …}
```

Routing on a guess is expensive: the Act path and the regulation path hit
different systems entirely (§4.5), and the wrong one 404s.

### 4.4 Amendment history

```
GET /v1/Versions?$filter=titleId eq 'C2004A04969'&$orderby=start desc&$top=10
```

```
C2024C00545  comp 47  2024-10-14 → (current)
C2024C00168  comp 46  2024-05-17 → 2024-10-14
C2024C00133  comp 45  2024-04-01 → 2024-05-17
```

Each row's `start`/`end` gives the exact window that version was in force —
useful for building a point-in-time archive rather than just "latest".

Point-in-time retrieval: `/v1/versions/find(titleid='…',asat=2023-06-30)`.

**Warning:** `/v1/Affect` and `/v1/_AffectsSearch` both returned **404** in live
testing (2026-07-29) despite being in the OpenAPI spec and the entity-set
index. `src/stage3_diff.py:_discover_amending_via_affect_api` treats them as a
last-resort fallback; right now that fallback is effectively dead. Don't build
on those two.

### 4.5 Getting a human-readable explanation of the change

Only needed if you want *why*, not just *what*. Route on the amending
instrument's `seriesType`:

**Regulations / instruments (`SR`, `SLI`)** — the Explanatory Statement is on
the FRL API. Fetch the *amending* instrument's ES, pinned at `AsMade` (the
instrument never changes, so `Latest` is wrong and 404s):

```python
for doc_type in ("ES", "SupplementaryES"):
    content = download_document(amending_id, doc_type=doc_type,
                                fmt="Word", as_at="AsMade")
    if content:
        break
```

ES documents run long. Truncate at the first standalone heading matching
`Attachment A`, `Schedule 1`, or `Notes on sections` — everything after those
is clause-by-clause detail:

```python
STOP = ("Attachment A", "Schedule 1", "Notes on sections")

def truncate_es(text: str) -> str:
    for i, line in enumerate(text.split("\n")):
        s = line.strip()
        if s and len(s) <= 100 and any(p.lower() in s.lower() for p in STOP):
            return "\n".join(text.split("\n")[:i]).strip()
    return text
```

**Acts** — no ES exists. `Titles('<id>').originatingBillUri` points to
ParlInfo (`parlinfo.aph.gov.au`), and you scrape from there. Four-tier
waterfall, best readability first (`_fetch_act_bill_summary`):

1. **Bills Digest** — Parliamentary Library, written for non-specialists.
   `…display.w3p;query=BillId_Phrase%3A%22{billId}%22%20Dataset%3Abillsdgs;rec=0`,
   extract between headings `Key Points` → `Contents`.
2. **Bill home page summary** — between `Summary` → `Progress of bill`,
   accepted if ≥ 100 words.
3. **Explanatory Memorandum** — between `General Outline` (or `Outline`) →
   `Financial Impact`. The EM URL contains a UUID assigned at upload and is
   **not derivable** from the bill id; regex it out of the bill home page HTML:
   `legislation%2Fems%2F`.
4. **Bill home summary again**, any length.

Match headings **line-anchored with a length cap** (≤80 chars, `re.fullmatch`),
or a paragraph that merely mentions "Summary" will match and you'll extract the
wrong section.

ParlInfo sits behind an **Azure WAF JS challenge** — plain `requests` gets a
challenge page. You need a real browser. Poll until the string `Azure WAF`
disappears from `page_source` rather than sleeping a fixed interval (resolution
is typically 0.5–3 s but varies):

```python
driver.get(url)
for _ in range(20):                       # 10 s at 0.5 s
    time.sleep(0.5)
    if "Azure WAF" not in driver.page_source:
        break
html = driver.page_source if len(driver.page_source) >= 500 else None
```

See `src/scraper.py:fetch_with_waf_polling` and `build_selenium_driver` for the
stealth Chrome setup (selenium-stealth, real four-part Chrome version in the UA
— `147.0.7727.117`, never `147.0.0.0`, which is a well-known automation tell).

---

## 5. Do not scrape the document text off the website

`https://www.legislation.gov.au/{titleId}/latest/text` renders its content
client-side. Measured on the Trade Marks Act 1995:

| Source | Extracted text |
|---|---|
| `/latest/text` via requests + trafilatura | **18 002 chars** — nav chrome, breadcrumbs, a table of contents |
| `documents/find(…type='Primary',format='Word')` | **348 772 chars** — the actual Act |

That is 5% of the document, and the 5% that contains no legal content. It looks
like a successful scrape: HTTP 200, no block signature, plausible-looking text
with the Act's name in it. Nothing fails loudly.

The `.gov.au` estate also runs WAFs that block cloud/CI egress IPs, so a
scraping approach that works from a laptop will fail from a runner.

**Use the API for text. Use a browser only for ParlInfo, where no API exists.**

---

## 6. Reference implementation map

| Concern | File |
|---|---|
| titleId extraction, `registerId` change probe | `src/stage1_metadata.py` (`_probe_frl`, `_extract_frl_title_id`) |
| Version + reasons, amending-instrument discovery, ES/bill retrieval | `src/stage3_diff.py` (`_fetch_frl_explainer` and helpers) |
| DOCX → text, WAF-aware browser fetch, stealth driver | `src/scraper.py` |
| Endpoint catalogue / OpenAPI spec | `docs/FRL-API/FRL_API_Index.json`, `FRL_Instructions.json` |
| Standalone ES downloader (original working prototype) | `docs/Reference-Code/download_es.py` |
| ParlInfo EM/summary scraper (original working prototype) | `docs/Reference-Code/fetch_em_summary (1).py` |

---

## 7. A snapshot-and-watch loop

```python
import json, pathlib, requests

API = "https://api.prod.legislation.gov.au"
STATE = pathlib.Path("frl_state.json")

def snapshot(title_id: str, out_dir: pathlib.Path) -> dict:
    s = requests.Session()
    v = frl_version(title_id, s)

    docx = download_document(title_id, doc_type="Primary", fmt="Word", session=s)
    if docx is None:                                    # API index miss
        date = v["start"][:10]
        docx = s.get(
            f"https://www.legislation.gov.au/{title_id}/latest/{date}/text/original/word",
            timeout=120, allow_redirects=True,
        ).content

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{title_id}_{v['registerId']}.docx").write_bytes(docx)
    (out_dir / f"{title_id}_{v['registerId']}.txt").write_text(
        docx_to_text(docx), encoding="utf-8")

    return {
        "title_id": title_id,
        "register_id": v["registerId"],
        "compilation_number": v["compilationNumber"],
        "start": v["start"],
        "has_unincorporated_amendments": v.get("hasUnincorporatedAmendments"),
        "reasons": v.get("reasons", []),
    }

def check(title_id: str, out_dir: pathlib.Path) -> str:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    prev = state.get(title_id, {})
    v = frl_version(title_id, requests.Session())

    if v["registerId"] == prev.get("register_id"):
        if v.get("hasUnincorporatedAmendments"):
            return "stale: amendments commenced but not yet compiled"
        return "unchanged"

    state[title_id] = snapshot(title_id, out_dir)
    STATE.write_text(json.dumps(state, indent=2))
    return f"amended: {prev.get('register_id')} → {v['registerId']}"
```

Design notes carried over from the pipeline:

- **Fail open.** If the probe errors, re-snapshot. Never treat an API failure
  as "unchanged" — that silently freezes the snapshot.
- **Only persist state after the download succeeds.** Writing the new
  `registerId` before the document lands means the next run sees "unchanged"
  against a baseline that was never captured.
- **Be polite.** One `Versions/Find` per document per day is plenty;
  compilations are registered on the order of weeks-to-months.
- **Verify size** against `sizeInBytes` from `/v1/Documents` before overwriting
  a good snapshot with a truncated download.
