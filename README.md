Here is a user-friendly `README.md` tailored for your repository, incorporating the specific logic, configuration, and automation details found in your uploaded files.

---

# IP First Response: Tripwire

**Tripwire** is a Python-based monitoring and change-detection engine designed to support the **IP First Response (IPFR)** platform.

Its primary mission is to autonomously monitor authoritative Intellectual Property sources—such as Australian Legislation and WIPO feeds—to detect updates that may impact IPFR content. When changes are detected, Tripwire archives the new content and logs the update, serving as the trigger for downstream impact assessment.

## 🚀 Key Features

* **Multi-Source Monitoring:** Capable of tracking diverse data sources including:
* **Legislation:** Australian Legislation OData API (prioritizing Word/PDF formats).
* **Web Scrapes:** Headless Selenium scraping for Javascript-heavy sites.
* **RSS/API:** Direct monitoring of XML/JSON feeds.


* **Intelligent Change Detection:** Uses SHA256 hashing to detect content changes while filtering out noise (e.g., dynamic timestamps or "Last updated" footers).
* **Automated Archiving:** Automatically downloads and saves changed content (DOCX, Markdown, XML) to a local `content_archive/` directory for version control.
* **Self-Healing:** Includes robust session management with retry logic for API stability.

## 📂 Repository Structure

* `tripwire.py`: The core logic script. Handles fetching, cleaning, hashing, and archiving data.
* `sources.json`: Configuration file defining *what* to monitor (URLs, priorities, output filenames).
* `requirements.txt`: Python dependencies required to run the engine.
* `tripwire_history.json`: A database of the last known state (hashes and timestamps) for every source.
* `content_archive/`: Directory where updated documents and scrapes are stored.
* `.github/workflows/tripwire.yml`: GitHub Actions configuration for automated scheduling.

## ⚙️ Configuration

The system is controlled via `sources.json`. You can add new sources by following this schema:

```json
{
  "name": "Name of Source",
  "type": "WebPage", 
  "url": "https://example.com/policy",
  "priority": "High",
  "output_filename": "policy_update.md"
}

```

### Supported Types:

* `Legislation_OData`: Queries the api.prod.legislation.gov.au endpoint.
* `WebPage`: Renders page via Selenium, cleans HTML, and converts to Markdown.
* `RSS` / `API`: Fetches raw XML or JSON data.

## 🛠️ Local Installation & Usage

### Prerequisites

* Python 3.9+
* Google Chrome (for Selenium scraping)

### Setup

1. **Clone the repository:**
```bash
git clone https://github.com/your-org/tripwire.git
cd tripwire

```


2. **Install dependencies:**
```bash
pip install -r requirements.txt

```


3. **Run the Tripwire:**
```bash
python tripwire.py

```



### Output

If updates are found:

1. New files are saved to `content_archive/`.
2. `tripwire_history.json` is updated with the new version hash and timestamp.
3. The console logs `[!] CHANGE DETECTED` for the relevant source.

## 🤖 Automation

This repository is configured with **GitHub Actions** to run autonomously.

* **Schedule:** Runs automatically every 6 hours (`0 */6 * * *`).
* **Workflow:**
1. Sets up Python and Chrome.
2. Executes `tripwire.py`.
3. If changes are detected (in history or archives), the Action automatically commits and pushes the updates back to the repository.



## 🧠 Logic Flow

Tripwire represents **Phase 1** of the IPFR content maintenance strategy.

1. **Trigger:** Tripwire detects an update.
2. **Priority Assessment:** The update is categorized (High/Medium/Low) based on `sources.json`.
3. **Future Phases:** (Planned) The system will summarize the change, identify influenced IPFR content, and verify if specific edits are required on the Drupal site.

## 🛡️ Credits

* **Legislation Data:** Sourced via the Federal Register of Legislation OData API.
* **Web Scraping:** Powered by `selenium-stealth` and `markdownify`.
