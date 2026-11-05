# AI Steward Dashboard - The Original Tripwire

A monitoring tool that helps Australian Public Servants keep track of changes to
GenAI platform policies, terms of service, and relevant AI governance guidance.

The dashboard automatically watches a curated set of government and private-sector
policy pages, detects when they change, uses Google Gemini to summarise and
prioritise each change, and publishes the results as a searchable web dashboard.

🔗 **Live dashboard:** https://thomas-amann-ipaustralia.github.io/ai-steward-dashboard

---

## How it works

The project has three parts that run together as an automated pipeline:

1. **Scraper (`main.py`)** — A Python script uses Selenium with `selenium-stealth`
   to fetch each monitored URL (falling back to a proxy when a site blocks direct
   access). It extracts the relevant content, computes an MD5 hash, and compares it
   against the previously stored hash to decide whether the page has changed.

2. **AI analysis** — When a change is detected, the old and new content are sent to
   Google Gemini (`gemini-2.5-flash`). The model returns a plain-language summary, a
   detailed markdown analysis, and a **priority** rating of `critical`, `high`,
   `medium`, or `low`. Results are saved as JSON.

3. **Dashboard (`src/`)** — A React single-page app reads the generated data files
   and presents the monitored policies grouped by category, with per-policy detail
   pages, change history, priority badges, and a dark-mode toggle.

The whole pipeline is orchestrated by GitHub Actions, which runs the scraper daily,
commits any new snapshots and analyses, rebuilds the React app, and deploys it to
GitHub Pages.

## What's monitored

Policy sources are configured in [`policy_sets.json`](policy_sets.json). Each entry
defines a named policy set, a category, and one or more URLs (optionally with a CSS
selector to target the relevant part of the page). The current sets cover:

- **Australian Government** — Digital.gov.au AI Policy, National Archives AI Policy,
  ACSC Information Security Manual (ISM)
- **State Government** — NSW Government AI Guidance
- **Private Sector** — Google, Anthropic, Perplexity, and Midjourney legal policies

To monitor a new source, add an entry to `policy_sets.json`:

```json
{
  "setName": "Example AI Policy",
  "category": "Private Sector",
  "urls": [
    { "url": "https://example.com/terms", "selector": "article" }
  ]
}
```

## Project structure

```
main.py              # Scraper + change detection + Gemini analysis
policy_sets.json     # Configuration of monitored policy sources
hashes.json          # Latest hash, category, and status for each policy set
snapshots/           # Latest extracted text content per policy set
analysis/            # Latest AI analysis (JSON) per policy set
logs/                # Historical snapshots and analyses of past versions
requirements.txt     # Python dependencies
src/                 # React dashboard (components, hooks, utils)
public/              # Static assets for the React app
.github/workflows/   # GitHub Actions automation
```

## Running locally

### Prerequisites

- Python 3.11+
- Node.js 20+
- Google Chrome (for Selenium)
- A Google Gemini API key

### Backend (scraper + analysis)

```bash
# Install Python dependencies
pip install -r requirements.txt

# Configure your environment (see .env.example)
export GEMINI_API_KEY="your-api-key"
# Optional proxy for sites with bot detection:
# export PROXY_HOST=... PROXY_PORT=... PROXY_USER=... PROXY_PASS=...

# Run a check
python main.py
```

The script updates `hashes.json` and writes to `snapshots/` and `analysis/`. On the
first run for a policy set it captures an initial snapshot; on later runs it only
calls Gemini when the content has actually changed.

> **Environment variables** are read from the process environment. Copy
> [`.env.example`](.env.example) as a reference for what to set. `GEMINI_API_KEY` is
> required; the `PROXY_*` variables are optional and only used to retry sites that
> block direct scraping.

### Frontend (dashboard)

```bash
# Install Node dependencies
npm install

# Start the development server
npm start        # http://localhost:3000

# Production build
npm run build
```

The React app fetches `hashes.json`, `analysis/`, and `snapshots/` from the site
root, so these data files need to be present in the build for the dashboard to
display content.

## Automation

Two GitHub Actions workflows live in `.github/workflows/`:

- **`update_checker.yml`** — Runs daily at midnight UTC (and on manual dispatch or
  pushes to `main` that touch app/config files). It installs Chrome and
  dependencies, runs `main.py`, commits any changes, builds the React app, and
  deploys it to GitHub Pages. Requires the `GEMINI_API_KEY` secret (and optional
  `PROXY_*` secrets).
- **`generate_lockfile.yml`** — A manual helper that regenerates
  `package-lock.json`.

## License

Released under the [MIT License](LICENSE).
