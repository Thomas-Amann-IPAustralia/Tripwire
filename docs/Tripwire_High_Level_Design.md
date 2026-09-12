# High Level Design — Tripwire
### Automated External Change Monitoring & Alerting Platform — Initial Deployment: IP First Response
**IP Australia | Internal Working Document**
**Date:** 30 June 2026
**Status:** Draft — architectural review + acquisition-alignment revision

---

> **Revision note (architectural review).** This revision reconciles the draft against the repository as the authoritative source and aligns the section hierarchy to the HLD template. Material corrections verified against the codebase: monitored source count 157 → **156** (54 FRL, 97 webpages, 5 RSS, per `source_registry.csv`); FRL API source count "55+" → **54**; the SQLite store has **10 tables**, with the chunk table correctly named `chunks` and the `ingestion_runs` audit table added (per `ingestion/db.py`); live-corpus scale stated as 139 IPFR pages (130 active); and the manual `publish-dashboard-data-release.yml` workflow added to the Implementation View. Structurally, the Sequence Diagram is nested under Component View, and Architecture Decisions, Risks and Issues, and Financial Impact are placed under Solution Design, matching the template. Per-section confidence summaries flag where a change was made; items that could not be completed from the repository alone are listed in the final section.

> **Revision note (acquisition alignment).** This revision generalises the document's framing to match the technology acquisition overview: Tripwire is presented as a reusable, configuration-driven external-change monitoring and alerting platform for IP Australia, with IP First Response (IPFR) as the immediate and primary deployment. Architecture and implementation claims remain grounded in the repository; broader-organisational context (other business units' needs, expressed stakeholder interest) is attributed to the acquisition overview. The functional requirements now trace to acquisition requirements **R1–R12**, and the instance-per-domain nature of multi-domain use is stated explicitly.

---

## Purpose

This document describes the architectural design of Tripwire — an automated external change monitoring and alerting platform developed for IP Australia. Its immediate deployment serves the IP First Response (IPFR) website, which has the pressing business need; but the platform is deliberately domain-agnostic. The sources it monitors and the content it maps changes against are defined entirely in configuration and data, so the same platform can serve other IP Australia content domains and business units with no changes to the underlying system. This document presents the solution through multiple architectural views to expose its concepts, constraints, and mechanics. It is not a statement of responsibilities or a deliverables list.

Tripwire is a nine-stage filter-funnel pipeline that continuously monitors a configurable set of authoritative external sources for substantive changes, assesses whether each change is relevant to a configured corpus of monitored content assets, and delivers prioritised, evidence-backed update recommendations to the responsible content owners. In its initial configuration the monitored corpus is the IPFR website and the sources are the IP legislative and guidance landscape; retargeting the platform to another content domain is a matter of configuration — the source registry, the corpus ingestion target, and notification routing — rather than a change to the platform code.

---

## Solution Context

### Business Problem / Opportunity

The IP First Response (IPFR) website, hosted at `ipfirstresponse.ipaustralia.gov.au`, provides accessible, plain-language IP guidance to Australian businesses and the public. Its accuracy depends on keeping pace with a broad and rapidly evolving landscape of authoritative sources: Acts of Parliament registered on the Federal Register of Legislation (FRL), associated Regulations, IP Australia practice manuals, court practice notes, WIPO publications, and a range of government and third-party guidance pages.

Maintaining accuracy across this landscape requires ongoing monitoring and content updates. Without automation, content owners must manually check 156 sources across varying cadences, assess whether each change is substantively relevant to IPFR guidance, and decide what (if anything) needs to be updated. This is time-consuming, error-prone, and scales poorly as both the source landscape and the IPFR corpus grow.

Tripwire addresses this by automating the detection-to-notification chain. It answers a progressive chain of questions — each more expensive to compute than the last — and delivers consolidated, evidence-backed amendment suggestions directly to content owners when a substantive, relevant change is detected.

While IPFR is the immediate driver, the underlying problem is not unique to it. Per the technology acquisition overview, many of IP Australia's business functions depend on maintaining information that reflects the current state of external sources — legislation, court decisions, regulatory guidance, and international IP standards. Keeping that information current through manual monitoring is operationally costly across the organisation and creates a standing risk of content drifting out of date and eroding user trust. Several internal stakeholders have already expressed interest in the platform for their own content domains. Tripwire is therefore scoped as a reusable organisational capability: built first for IPFR, where the need is most pressing, but applicable wherever IP Australia maintains content that must stay aligned with a changing external environment.

**Confidence summary:** The IPFR business problem is clearly and explicitly stated in the system plan (Section 1) and is consistent with the source registry and the IPFR site reference throughout the codebase. The broader-organisational framing and the statement of expressed stakeholder interest are drawn from the **technology acquisition overview** (not the repository) and are attributed as such. *Reviewer correction (earlier review):* the source count was reduced from 157 to **156** — `source_registry.csv` contains 156 data rows (54 FRL, 97 webpages, 5 RSS); the original figure had counted the header row. **Confidence: 9/10** (IPFR problem 10/10; broader framing as stated in the overview).

---

### Scope

Scope is expressed in two layers: the **platform capability** (domain-agnostic, the subject of this acquisition) and the **initial IPFR deployment** (the first configured instance of that platform).

#### In Scope — Platform Capability

- A configuration-driven monitoring platform whose monitored sources, target content corpus, and monitoring schedules are defined entirely in configuration and data (`source_registry.csv`, `tripwire_config.yaml`, and the ingested corpus database) — supporting extension to additional content domains and business units **without changes to the platform code** (acquisition requirement R12)
- Nine-stage filter-funnel pipeline (Stages 1–9): change detection, significance filtering, change characterisation, relevance assessment against the configured corpus, impacted-asset identification, cross-event consolidation, LLM-based recommendation generation, and content-owner notification
- Corpus ingestion pipeline: automated refresh of the SQLite corpus database used for semantic matching, from a configurable sitemap source
- Observation mode for calibration before going live; deferred-trigger resilience when the LLM API is unavailable; structured feedback capture; weekly observability reporting; health alerting; and an authenticated operational dashboard
- Human-in-the-loop model throughout: the platform recommends and routes; it never publishes or edits content

#### In Scope — Initial Deployment (IP First Response)

- Automated daily monitoring of the 156 sources registered for IPFR in `data/influencer_sources/source_registry.csv` (54 Federal Register of Legislation entries, 97 government and third-party webpages, 5 RSS feeds), spanning:
  - Federal Register of Legislation (FRL) sources: Acts and Regulations accessed via the FRL REST API
  - Government and third-party webpages (IP Australia, courts, WIPO, ASBFEO, e-commerce platforms, etc.)
  - RSS feeds (Federal Court practice notes, WIPO news, EUIPO enforcement news)
- IPFR corpus ingestion (daily refresh), LLM-powered assessment of whether IPFR pages require amendment, and consolidated email notification to the IPFR content owner(s) with structured amendment suggestions and supporting evidence

#### Out of Scope

- Publication or automated editing of content (human action is always required; the platform recommends, never acts)
- Monitoring of sources outside the configured source registry
- Access to any non-public or authenticated sources
- Real-time (sub-daily) monitoring
- Training or fine-tuning of ML models
- Replacement or modification of any content management system
- Centralised orchestration of multiple content domains within a single pipeline instance: multi-domain use is achieved by deploying **additional, independently-configured instances** (each with its own source registry, corpus, and notification routing). A shared multi-tenant control plane is a possible future extension, not part of the current design.

**Confidence summary:** Platform-capability and initial-deployment in-scope items are verifiable from the source registry CSV (156 sources), system plan, codebase, configuration file, and GitHub Actions workflows; the configuration-driven extensibility (R10, R12) is evidenced by the fact that sources, corpus target, schedules, and notification routing are all config/data rather than code. Out-of-scope items are inferred from the system's architecture (human-in-the-loop model, no CMS integration, single-corpus-per-instance design) and the plan's explicit exclusions. **Confidence: 9/10**

---

### Architecturally Significant Requirements

#### Functional Requirements

The acquisition overview defines twelve functional requirements (R1–R12). The table below states each, its MoSCoW priority, and the solution element that realises it, providing requirement traceability from the acquisition request through to the implementation. The requirements are domain-neutral ("monitored content assets"); in the initial deployment those content assets are IPFR pages.

| ID | Acquisition Requirement | Priority (MoSCoW) | Realised By |
|---|---|---|---|
| R1 | Monitor authoritative external sources for changes | Must | Stage 1 metadata probe over the configured `source_registry.csv` |
| R2 | Detect when source content changes | Must | Stage 2 — SHA-256 hash comparison and word-level diff |
| R3 | Filter insignificant or noise-level changes | Must | Stage 2 significance fingerprint; Stage 4 relevance gate |
| R4 | Identify what has changed and characterise the nature of the change | Must | Stage 3 — diff generation, FRL Explanatory Statement retrieval, RSS new-item extraction; characterised further in Stage 8 |
| R5 | Assess relevance of a change to monitored content assets | Must | Stage 4 (YAKE-BM25 + bi-encoder RRF), Stage 5 (bi-encoder), Stage 6 (cross-encoder) |
| R6 | Identify which content assets are impacted | Must | Stages 5–6 page/chunk matching; Stage 6 graph propagation to related assets |
| R7 | Consolidate related change events into a single actionable signal | Must | Stage 7 — trigger aggregation, grouping all (source, asset) pairs per content asset |
| R8 | Generate content update recommendations with supporting evidence | Must | Stage 8 — structured LLM verdict, suggested changes, and supporting evidence (scores, diffs, source links) |
| R9 | Notify content owners of required actions | Must | Stage 9 — consolidated email per run to the responsible content owner |
| R10 | Support configurable sources, content repositories, and monitoring schedules | Must | `source_registry.csv`, `tripwire_config.yaml`, ingestion `sitemap_url`, per-source `check_frequency` |
| R11 | Provide reporting and operational visibility | Should | `observability.py` weekly reports, `health.py` alerting, the `pipeline_runs` log, and the Render dashboard |
| R12 | Support extension to additional content domains through configuration, without platform changes | Must | Domain-agnostic core; one module per stage; sources, corpus, schedules and routing are all config-driven; the "forkable" design intent stated in CLAUDE.md |

The following derived functional requirements elaborate R1–R12 with implementation-specific behaviour evidenced in the codebase and system plan, stated in domain-neutral terms with initial-deployment specifics in brackets:

| Requirement Statement | Priority (MoSCoW) |
|---|---|
| The system must detect changes to all registered sources on their configured schedules (daily, weekly, fortnightly, monthly, or quarterly) | Must |
| The system must filter cosmetic or insignificant changes before performing semantic matching | Must |
| The system must determine whether a detected change is relevant to the configured content corpus (IPFR pages in the initial deployment) | Must |
| The system must identify which specific content assets are most likely affected by each relevant change | Must |
| The system must deliver a single, consolidated notification per run to the responsible content owner, containing all recommendations and supporting evidence | Must |
| The system must produce a structured LLM verdict (`CHANGE_REQUIRED`, `NO_CHANGE`, or `UNCERTAIN`) for each impacted content asset with grouped triggers | Must |
| The system must operate in an observation mode (no LLM calls, no notifications) for calibration during initial deployment of any new domain | Must |
| The system must store deferred trigger bundles and retry LLM assessment on the next run when the LLM API is unavailable | Must |
| The system must send health alert emails to the operator when error thresholds are exceeded | Must |
| The system must support a structured feedback mechanism so content owners can rate alert quality | Should |
| The system must produce weekly observability reports summarising score distributions and alert volume | Should |
| The system must expose a monitoring dashboard with run history, source status, and alert counts | Should |
| The system should support the addition of new sources without code changes (registry configuration only) | Should |
| The system could support graph-propagated alerts to related content assets through a quasi-graph of asset relationships | Could |

#### Non-Functional Requirements

| Requirement Statement | Priority (MoSCoW) |
|---|---|
| The pipeline must complete within 30 minutes per run under expected load (156 sources; the plan estimates ~25 pass Stage 1, ~10 pass Stage 2, and ~5 reach Stage 5) | Must |
| The system must fail-closed: uncertain or unclassifiable signals must be escalated, never silently dropped | Must |
| All credentials and secrets must be stored as encrypted GitHub Actions secrets, never in version-controlled files | Must |
| The system must be deployable without a dedicated server, using only GitHub Actions for compute | Must |
| The system must use only SQLite as its database technology | Must |
| The system must not use asynchronous Python | Must |
| The system must be operable by a single administrator without specialist ML or infrastructure skills | Should |
| The system must cache model weights to avoid re-downloading ~1 GB of model files on each run | Should |
| All tuneable thresholds and parameters must be version-controlled in a single YAML configuration file | Should |
| The system should tolerate individual source failures without blocking processing of other sources | Must |
| The dashboard must require authentication before exposing any data | Must |

**Confidence summary:** The R1–R12 requirements are taken verbatim from the **technology acquisition overview**; each "Realised By" mapping was verified against the pipeline stages and configuration in the codebase. The derived functional requirements are drawn directly from the system plan's stage specifications and the implementation. Non-functional requirements are evidenced in code (`timeout-minutes`, WAL mode, synchronous-only Python constraint, single YAML config, `try/except` per-source isolation), GitHub Actions workflow configuration, and the system plan's design decisions; the load estimates trace to the runtime budget in Section 6.6 of the plan. *Reviewer note:* the template directs authors to cross-reference the IP Australia Enterprise NFR catalogue. That catalogue is not present in the repository, so the NFRs above are derived from the system's own constraints and design decisions rather than mapped to enterprise NFR identifiers — an Enterprise Architect should complete that mapping. **Confidence: 9/10** (R1–R12 mapping); **8/10** (NFRs).

---

### Architecture Principles

The following principles govern the solution design. These are drawn from the explicit design decisions stated in the system plan and CLAUDE.md, and from the implementation itself. Alignment with any IP Australia Enterprise Architecture Principles could not be assessed, as those documents were not available to this author.

| Architecture Principle | Compliance |
|---|---|
| **Fail-closed:** uncertain signals are escalated, never silently dropped. The LLM is instructed to produce `UNCERTAIN` rather than guess; deferred triggers are retried rather than discarded. | Compliant — implemented in Stage 8 prompt design, deferred trigger mechanism, and the `UNCERTAIN` verdict pathway in Stage 9 |
| **Single source of truth for configuration:** all thresholds, model identifiers, and behavioural parameters are in one version-controlled YAML file (`tripwire_config.yaml`). No configuration is duplicated across environment variables. | Compliant — enforced by `config.py` validation at pipeline start |
| **Filter-funnel — cheap before expensive:** each pipeline stage acts as a gate. Expensive operations (semantic inference, LLM API calls) are only reached by the small fraction of changes that survive cheap upstream checks. | Compliant — evidenced by lazy model loading (Section 7.4 of system plan) and per-stage filtering throughout `pipeline.py` |
| **Modularity & reusability (configuration-driven):** one Python module per responsibility, over a domain-agnostic core. Monitored sources, the target content corpus, monitoring schedules, and notification routing are all defined in configuration and data (`source_registry.csv`, `tripwire_config.yaml`, the ingested corpus) — so the platform can be retargeted to a different content domain or business unit by configuration alone, with no platform code change. Realises acquisition requirement R12. | Compliant — one file per stage with `ingestion/`/`src/` separation; sources, corpus, schedules and routing are all config/data; the "forkable" design intent is stated explicitly in CLAUDE.md |
| **No unnecessary external dependencies:** SQLite is the only database; standard-library modules are preferred; well-audited packages only. | Compliant — `requirements.txt` is minimal; no web frameworks, no message queues, no cloud SDKs |
| **Synchronous processing:** no async/await. Predictable resource use and linear execution make the pipeline easy to debug and extend. | Compliant — enforced as a hard constraint in CLAUDE.md |
| **Audit trail through Git:** all pipeline state (influencer snapshots, SQLite corpus) is committed to the repository after each run, providing a complete history of what was seen and when. | Compliant — implemented in `_git_commit_snapshots()` and the GitHub Actions workflow commit step |

**Confidence summary:** These principles are stated verbatim or directly inferrable from the system plan (Section 1), CLAUDE.md, and the implementation. However, I cannot assess alignment with the IP Australia Enterprise Architecture Principles or Business Capability Model without access to those documents. **Confidence: 8/10**

---

### Impacted Users

| User Group | Description | Interaction Mode |
|---|---|---|
| **Content owner(s)** | IP Australia staff responsible for the accuracy of a monitored content domain; in the initial deployment, the IPFR content owner(s). The primary consumers of Tripwire's output. | Receive consolidated notification emails with update recommendations; provide feedback via mailto reply links |
| **System operator / administrator** | The technical custodian responsible for monitoring pipeline health, adjusting thresholds, adding sources, and responding to failures. May be the same person as the content owner during initial deployment. | Receive health alert emails; access the Render dashboard; run manual `workflow_dispatch` pipeline runs |
| **Other IP Australia business units (prospective)** | Teams responsible for other content domains that must stay aligned with external sources (e.g., policy guidance, other public content). Per the acquisition overview, several internal stakeholders have already expressed interest in the platform. | Future adopters; each would operate an independently-configured instance with its own sources, corpus, and notification routing |
| **End users of the deployed domain (indirect)** | Members of the public, businesses, and IP practitioners who rely on the published content — in the initial deployment, the IPFR website — for accurate, up-to-date guidance. | Not direct users of Tripwire; benefit from the improved accuracy and timeliness of the content |

> **Note:** Headcount, locations, and working arrangements for content owners — both for IPFR and for prospective adopting business units — are not available in the repository or the acquisition overview. The business owner should expand this section with that detail, including accessibility requirements for the notification format.

**Confidence summary:** The content owner and operator roles are clearly established by the notification model (Stage 9 email), the health alert configuration, and the runbooks. The prospective adoption by other business units is drawn from the **acquisition overview** and is attributed as such. End users as indirect beneficiaries are inferred from the stated purpose. Exact headcount and location data are not available. **Confidence: 6/10**

---

### Impacted Systems

| System | Type | Role in Solution | Support / Licensing |
|---|---|---|---|
| **IP First Response website** (`ipfirstresponse.ipaustralia.gov.au`) | Internal — IP Australia | Source of the monitored IPFR content corpus; scraped daily by the ingestion pipeline | IP Australia owned; existing system |
| **Federal Register of Legislation (FRL) API** (`api.prod.legislation.gov.au`) | External — Attorney-General's Department | Source of structured legislative change information; accessed via official REST API for the 54 Federal Register of Legislation sources | Public API; no licensing required; breaking changes possible without notice |
| **OpenAI API** | External — commercial | LLM assessment (Stage 8); produces structured JSON verdicts on IPFR amendment requirements | Requires API key (OPENAI_API_KEY); commercial pricing applies; data processing terms apply |
| **Gmail (SMTP / IMAP)** | External — Google | SMTP for outbound notification and health alert emails; IMAP for ingesting feedback replies | Requires Gmail App Passwords; subject to Google's terms and rate limits |
| **GitHub Actions** | External — GitHub | All pipeline compute (ingestion, main pipeline, feedback ingestion); persists state via Git commits | Subject to GitHub Actions minutes limits (free for public repos; organisational plan otherwise) |
| **Hugging Face Model Hub** | External — Hugging Face | Source of pre-trained ML model weights: `BAAI/bge-base-en-v1.5` (bi-encoder, ~400 MB) and `Alibaba-NLP/gte-reranker-modernbert-base` (cross-encoder, ~600 MB) | Open-source models; free to download; cached after first use |
| **Render** | External — commercial PaaS | Hosts the Tripwire monitoring dashboard (Node/Express + React) | Free tier or Starter (~$7/month); data persisted to a Render persistent disk |
| **spaCy / en_core_web_sm** | External — open-source | Named entity recognition (NER) in Stages 2 and ingestion enrichment | Open-source (MIT); downloaded as part of workflow setup |

> **Note:** Target state alignment (whether each component aligns with IP Australia's Enabling Platform Roadmaps) could not be assessed. The Enterprise Architect should review this table against current roadmaps.

**Confidence summary:** All impacted systems are identifiable from the source registry, requirements files, GitHub Actions workflow configuration, and system plan. Licensing and support details are inferred from the nature of each service; formal support arrangements (SLAs, vendor agreements) are not documented in the repository. **Confidence: 7/10**

---

### Assumptions, Dependencies and Constraints

#### Assumptions

- The IPFR website's sitemap structure and trafilatura-extractable content format remain sufficiently stable to support daily automated scraping.
- The FRL REST API (`api.prod.legislation.gov.au`) maintains backward compatibility with the current version and document endpoints used by Stage 3.
- GitHub Actions Ubuntu runners provide at least 7 GB RAM and 2 CPU cores, sufficient for CPU-only inference of the two ML models within the 30-minute timeout.
- The combined size of the SQLite database and tracked files does not exceed GitHub's per-file size limits during the initial deployment period. (The plan specifies migration to Git LFS if the SQLite file exceeds ~50 MB.)
- The content owner has an email client capable of generating mailto: reply links with pre-populated body text for the feedback mechanism.
- The system will run in observation mode for 4–8 weeks before going live, and manual snapshot alteration tests will be conducted to calibrate thresholds.
- A residential proxy URL will be supplied as a GitHub Actions secret when required for gov.au sources that block GitHub Actions runner IPs.

#### Dependencies

| Dependency | Description | Risk if Unavailable |
|---|---|---|
| GitHub Actions | All pipeline compute | Complete pipeline outage |
| OpenAI API | LLM assessment (Stage 8) | Triggers deferred; no email sent; resolved on next run |
| FRL API | Legislative change explainer retrieval (Stage 3 for FRL sources) | Falls back to webpage diff; downgraded quality |
| Gmail (SMTP) | Outbound email delivery | Email written to fallback file; operator health alert sent |
| Hugging Face model weights | Semantic matching (Stages 4–6) | Cached on GitHub Actions; cache miss triggers re-download (~2 min delay) |
| IPFR website | Daily corpus refresh | Ingestion skipped; pipeline runs against stale corpus |

#### Constraints

- **No GPU:** GitHub Actions Ubuntu runners are CPU-only. PyTorch must be installed from the CPU index. Model inference is CPU-bound.
- **SQLite only:** No PostgreSQL, MongoDB, or other database technology.
- **No async Python:** Synchronous code only throughout all pipeline modules.
- **30-minute timeout:** The `tripwire.yml` workflow has a 60-minute `timeout-minutes` (plan specifies 30 minutes; the actual workflow uses 60 minutes as a wider safety net).
- **No web framework:** No Flask, FastAPI, or Django within the pipeline; the dashboard uses Node/Express separately.
- **Python 3.11+** required; no compatibility with older versions.
- **Single configuration file:** All parameters in `tripwire_config.yaml`; environment variables are used only for secrets, never for configuration.

**Confidence summary:** Assumptions are inferred from the design constraints documented in the system plan and codebase. Dependencies are directly evidenced by the workflow definitions and source code imports. Constraints are explicitly stated in CLAUDE.md and the system plan. **Confidence: 9/10**

---

## Solution Design

### Business Capability View

Tripwire introduces a reusable **External Change Intelligence** capability for IP Australia: continuous, evidence-based awareness of changes in an external source landscape, mapped to the organisation's content assets and delivered in a form that enables efficient editorial decision-making. It augments the **Content Management** capability wherever content must stay aligned with a changing external environment. The immediate beneficiary is IPFR (the IP legislative and guidance landscape mapped to the IPFR website); per the acquisition overview, the same capability applies to any content domain the organisation maintains.

The platform operates across three capability layers, each domain-agnostic:

1. **Source Monitoring:** Automated, scheduled observation of a configured set of authoritative sources across legislation, government webpages, and RSS feeds (156 sources in the initial IPFR configuration). Replaces manual monitoring effort.
2. **Relevance Assessment:** Multi-stage semantic and lexical comparison between detected changes and the configured content corpus (the IPFR corpus initially), surfacing only the changes most likely to require editorial attention.
3. **Editorial Support:** Structured, LLM-generated update recommendations with supporting evidence (scoring data, diff text, source links) delivered directly to the responsible content owners. Includes a feedback loop for continuous quality improvement.

> **Note:** A formal reference to the IP Australia Business Capability Model could not be made, as that document was not available to this author. The Enterprise Architect should map these capability areas against the authoritative model — including whether "External Change Intelligence" is best expressed as a new capability or an enhancement to Content Management.

**Confidence summary:** The capability framing is derived from the system's purpose and operational flow (repository) and the reusable-capability positioning in the **acquisition overview**; the three layers are evidenced by the pipeline stages. Specific alignment with the IP Australia Business Capability Model cannot be verified without that document. **Confidence: 6/10**

---

### Component View

The following diagram describes the logical system components and their relationships.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  IPFR INGESTION PIPELINE  (runs daily at 01:00 UTC)                         │
│                                                                              │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐  │
│  │  sitemap.py  │→  │scrape_ipfr.py│→  │   enrich.py  │→  │   graph.py   │  │
│  │ (sitemap CSV)│   │(plain text + │   │(chunks, BGE  │   │(quasi-graph  │  │
│  │              │   │ XML offsets) │   │embeddings,   │   │edges: embed  │  │
│  │              │   │              │   │NER, YAKE)    │   │+ entity OL)  │  │
│  └─────────────┘   └──────────────┘   └──────────────┘   └──────────────┘  │
│         ↓                   ↓                  ↓                  ↓          │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  ipfr.sqlite  (pages, chunks, entities, keyphrases, graph_edges,      │  │
│  │                sections, pipeline_runs, deferred_triggers,            │  │
│  │                llm_assessments, ingestion_runs)   [10 tables]         │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │  (corpus read by main pipeline)
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  MAIN MONITORING PIPELINE  (runs daily at 02:00 UTC)                        │
│                                                                              │
│  source_registry.csv → pipeline.py (orchestrator)                           │
│                                                                              │
│  Per-source (Stages 1–6):                                                   │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐                │
│  │  Stage 1  │→ │  Stage 2  │→ │  Stage 3  │→ │  Stage 4  │                │
│  │ Metadata  │  │  Change   │  │   Diff    │  │ Relevance │                │
│  │   Probe   │  │ Detection │  │Generation │  │  Scoring  │                │
│  │(ETag, LM, │  │(SHA-256,  │  │(diff file,│  │(YAKE-BM25 │                │
│  │ FRL ver.) │  │word diff, │  │FRL ES doc,│  │+bi-encoder│                │
│  │           │  │sig. tag)  │  │RSS items) │  │RRF fusion)│                │
│  └───────────┘  └───────────┘  └───────────┘  └───────────┘                │
│                                                                              │
│  ┌───────────┐  ┌───────────┐                                               │
│  │  Stage 5  │→ │  Stage 6  │                                               │
│  │Bi-encoder │  │Cross-enc. │                                               │
│  │ (coarse   │  │(precise + │                                               │
│  │ semantic  │  │ graph     │                                               │
│  │  match)   │  │propagation│                                               │
│  └───────────┘  └───────────┘                                               │
│                                                                              │
│  Cross-source aggregation (Stage 7):                                        │
│  ┌────────────────────────────────┐                                         │
│  │  stage7_aggregation.py         │                                         │
│  │  Groups triggers by IPFR page  │                                         │
│  └────────────────────────────────┘                                         │
│                                                                              │
│  Per-page (Stages 8–9, skipped in observation mode):                        │
│  ┌───────────┐  ┌───────────┐                                               │
│  │  Stage 8  │→ │  Stage 9  │                                               │
│  │   LLM     │  │   Email   │                                               │
│  │Assessment │  │Notification│                                              │
│  │(OpenAI    │  │(SMTP via  │                                               │
│  │structured │  │  Gmail)   │                                               │
│  │   JSON)   │  │           │                                               │
│  └───────────┘  └───────────┘                                               │
│                                                                              │
│  Cross-cutting: config.py, errors.py, retry.py, scraper.py, validation.py  │
│                 health.py, observability.py                                 │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────┐     ┌──────────────────────────┐
│  FEEDBACK INGESTION      │     │  DASHBOARD               │
│  (every 6 hours)         │     │  (Render, always-on)     │
│                          │     │                          │
│  feedback_ingestion.py   │     │  Node/Express + React    │
│  Gmail IMAP → UNSEEN     │     │  Basic Auth protected     │
│  [TRIPWIRE] messages →   │     │  Reads ipfr.sqlite and   │
│  feedback.jsonl          │     │  source_registry.csv     │
└──────────────────────────┘     └──────────────────────────┘
```

| Component | Owner | Functions | Target State Alignment |
|---|---|---|---|
| `ingestion/` — IPFR Ingestion Pipeline | IP Australia (Tripwire team) | Scrapes IPFR sitemap and pages; enriches with BGE embeddings, NER (spaCy), YAKE keyphrases; builds quasi-graph; populates SQLite | Unknown — to be assessed against IP Australia roadmaps |
| `src/pipeline.py` — Main Orchestrator | IP Australia (Tripwire team) | Executes Stages 1–9 in sequence for each due source; handles deferred triggers; commits state to Git | Unknown |
| `src/stage1_metadata.py` | IP Australia (Tripwire team) | HTTP HEAD / FRL API version probe; determines whether source has changed at all | Unknown |
| `src/stage2_change_detection.py` | IP Australia (Tripwire team) | SHA-256 hash comparison; word-level diff; significance fingerprint tagger (spaCy + regex) | Unknown |
| `src/stage3_diff.py` | IP Australia (Tripwire team) | Diff file generation (webpages); FRL Explanatory Statement retrieval via REST API; RSS new-item extraction | Unknown |
| `src/stage4_relevance.py` | IP Australia (Tripwire team) | YAKE keyword extraction; BM25 ranking against IPFR corpus; bi-encoder cosine similarity; weighted RRF fusion; source importance multiplier | Unknown |
| `src/stage5_biencoder.py` | IP Australia (Tripwire team) | Chunk incoming diff; cosine similarity against pre-computed IPFR chunk embeddings (BGE); coarse candidate selection | Unknown |
| `src/stage6_crossencoder.py` | IP Australia (Tripwire team) | Full cross-encoder scoring (gte-reranker-modernbert-base); reranking with lexical and graph signals; graph propagation (up to 3 hops, decay 0.45) | Unknown |
| `src/stage7_aggregation.py` | IP Australia (Tripwire team) | Groups all confirmed (source, page) pairs into TriggerBundles per IPFR page ID | Unknown |
| `src/stage8_llm.py` | IP Australia (Tripwire team) | Single OpenAI API call per TriggerBundle; structured JSON schema validation; retry on validation failure; deferred trigger storage on API failure | Unknown |
| `src/stage9_notification.py` | IP Australia (Tripwire team) | Composes and sends consolidated email (SMTP/Gmail); includes feedback mailto links; writes fallback file on SMTP failure | Unknown |
| `src/feedback_ingestion.py` | IP Australia (Tripwire team) | Gmail IMAP polling; parses structured feedback replies; appends to `feedback.jsonl` | Unknown |
| `src/health.py` | IP Australia (Tripwire team) | Evaluates post-run health conditions; sends operator alert emails | Unknown |
| `src/observability.py` | IP Australia (Tripwire team) | Queries SQLite `pipeline_runs`; generates weekly score distribution report | Unknown |
| `tripwire_config.yaml` | IP Australia (Tripwire team) | Single version-controlled configuration file; all thresholds and parameters; validated at pipeline start | Unknown |
| `data/influencer_sources/source_registry.csv` | IP Australia (content team) | Registry of all 156 monitored sources: URL, type, importance (0–1), check frequency, force_selenium flag | Unknown |
| `data/ipfr_corpus/ipfr.sqlite` | IP Australia (Tripwire team) | SQLite corpus database: 10 tables including pages, chunks, embeddings, NER, graph edges, run logs, and LLM assessments | Unknown |
| `dashboard/` — Monitoring Dashboard | IP Australia (Tripwire team) | Node 20 / Express API + React/Vite frontend; hosted on Render; Basic Auth protected; displays run history, source health, alerts, config | Unknown |
| FRL API (`api.prod.legislation.gov.au`) | Attorney-General's Department | Provides structured legislative change information for FRL sources | External |
| OpenAI API | OpenAI | LLM assessment service (Stage 8); accepts structured prompts, returns JSON verdicts | External |
| Gmail (SMTP/IMAP) | Google | Outbound email delivery; inbound feedback reply parsing | External |

#### Change Description

This is a net-new system. There is no existing automated monitoring capability being replaced. Tripwire introduces a fully automated monitoring, assessment, and notification pipeline where previously manual effort was required.

The phased implementation plan in the system plan (Phases 1–5) provides the transitional roadmap. At time of writing, the system has completed all nine pipeline stages and is running in live mode (`observation_mode: false`). The deferred Phase 5 tasks (threshold calibration from feedback data, grid search on relevance weights, internal link graph edges) require accumulated production data before they can be executed.

**Transition to additional domains.** Onboarding a further content domain does not change any platform component listed above; it is a configuration-and-data exercise — register that domain's sources, point corpus ingestion at that domain's content, and set the notification routing. Each domain runs as its own configured instance (its own corpus database and scheduled workflows), typically beginning in observation mode for calibration. A shared control plane to manage many instances centrally is a candidate future component, not part of the current design.

**Confidence summary:** The component table is exhaustively evidenced by the actual source files in `src/` and `ingestion/`, the GitHub Actions workflows, the system plan, and the dashboard DEPLOY.md. *Reviewer corrections:* the SQLite store holds **10 tables** — the chunk table is named `chunks` (not `page_chunks`) and an `ingestion_runs` audit table was added to the listing, both confirmed against `ingestion/db.py`; the source count is **156**. Target State Alignment cannot be completed without IP Australia's roadmap documents. **Confidence: 9/10** (for components described); **0/10** (for Target State Alignment — not completable without roadmap documents).

#### Sequence Diagram

The following describes the primary processing flow for a single influencer source that triggers an IPFR page amendment alert.

```
GitHub Actions           pipeline.py          External Source      OpenAI API        Content Owner
     |                       |                      |                   |                  |
     |-- 02:00 UTC trigger -->|                      |                   |                  |
     |                       |-- load config ------->|                   |                  |
     |                       |-- load source registry|                   |                  |
     |                       |                      |                   |                  |
     |   [For each due source]                      |                   |                  |
     |                       |-- Stage 1: HEAD / FRL version probe ---->|                  |
     |                       |<------ signals: changed / unchanged ------|                  |
     |                       |                      |                   |                  |
     |                       |-- Stage 2: scrape + SHA-256 + word diff  |                  |
     |                       |   [cosmetic/identical → SKIP]            |                  |
     |                       |                      |                   |                  |
     |                       |-- Stage 3: generate diff / FRL ES / RSS items               |
     |                       |                      |                   |                  |
     |                       |-- Stage 4: YAKE + BM25 + bi-encoder + RRF fusion            |
     |                       |   [no relevant candidates → SKIP]        |                  |
     |                       |                      |                   |                  |
     |                       |-- Stage 5: bi-encoder chunk comparison   |                  |
     |                       |   [no pages above threshold → SKIP]      |                  |
     |                       |                      |                   |                  |
     |                       |-- Stage 6: cross-encoder + graph propagation               |
     |                       |   [below CE threshold → SKIP]            |                  |
     |                       |                      |                   |                  |
     |   [After all sources processed]              |                   |                  |
     |                       |-- Stage 7: aggregate triggers by IPFR page                 |
     |                       |                      |                   |                  |
     |   [For each IPFR page with triggers]         |                   |                  |
     |                       |-- Stage 8: prompt (diffs + IPFR page + scores) -->|        |
     |                       |<-------- JSON: verdict / confidence / suggestions --|       |
     |                       |-- validate JSON schema; retry once if invalid        |      |
     |                       |-- if still invalid: store in deferred_triggers        |     |
     |                       |                      |                   |                  |
     |                       |-- Stage 9: compose consolidated email                ----->|
     |                       |                      |                   |   (reads email)  |
     |                       |-- commit snapshots + LLM reports to Git |                  |
     |                       |-- log pipeline_runs to SQLite            |                  |
     |                       |-- health.py: evaluate + alert if needed  |                  |
     |<---- workflow complete |                      |                   |                  |
```

**Confidence summary:** The sequence is directly traceable through `pipeline.py` (the orchestrator code is fully implemented and readable). The flow matches the system plan Section 2.1 and 3.x exactly. **Confidence: 9/10**

---

### Information View

The system manages the following categories of information:

#### Persistent Data Stores

| Asset | Location | Format | Description |
|---|---|---|---|
| IPFR corpus database | `data/ipfr_corpus/ipfr.sqlite` | SQLite (binary, WAL mode) | 10 tables: `pages`, `chunks`, `entities`, `keyphrases`, `graph_edges`, `sections`, `pipeline_runs`, `deferred_triggers`, `llm_assessments`, `ingestion_runs`. Committed to Git after each ingestion run. |
| Influencer source snapshots | `data/influencer_sources/snapshots/<source_id>/` | Plain text (`<source_id>.txt`), JSON (`state.json`), versioned text (`.v1.txt`–`.v6.txt`) | Current content snapshot and state (hash, last probe signals, last checked timestamp) for each monitored source. Committed after each pipeline run. |
| LLM assessment reports | `data/LLM Reports/<run_id>_<page_id>.json` | JSON | One file per (run, IPFR page) pair with full LLM verdict, reasoning, suggested changes, token counts, and confidence. Committed after each run. |
| Feedback log | `data/logs/feedback.jsonl` | JSONL | Structured feedback from content owner email replies: run_id, page_id, source_id, feedback category, free-text comment, ingestion timestamp. Appended by feedback ingestion workflow. |
| IPFR sitemap | `data/ipfr_corpus/sitemap.csv` | CSV | Registry of IPFR pages: URL, title, page_id, snapshot link, last modified date, last checked date. |
| Source registry | `data/influencer_sources/source_registry.csv` | CSV | 156 monitored sources: source_id, URL, title, type, importance, check_frequency, notes, force_selenium flag. |
| Observation summaries | `data/logs/observation_summary_<run_id>.json` | JSON | Per-run score distributions and trigger counts during observation mode. |
| Health alert fallback | `data/logs/health_alert_<run_id>.txt` | Plain text | Written when SMTP health alert cannot be delivered. |
| Tripwire configuration | `tripwire_config.yaml` | YAML | All tuneable parameters; version-controlled. A snapshot is embedded in every `pipeline_runs.details` record. |

#### SQLite Schema Summary

```
pages           — IPFR page metadata, content, document embedding (BGE), status
chunks          — Pre-chunked content with BGE chunk-level embeddings
entities        — Named entities (ORG, PERSON, LAW, etc.) per IPFR page
keyphrases      — YAKE keyphrases with IDF weights per IPFR page
graph_edges     — Quasi-graph edges: type (embedding_similarity / entity_overlap / internal_link), weight
sections        — Heading hierarchy and character offsets per IPFR page
pipeline_runs   — One row per (run, source): stage reached, outcome, triggered pages, full details JSON
deferred_triggers — LLM-pending trigger bundles awaiting retry
llm_assessments — LLM verdict, confidence, reasoning, suggested changes, token counts per (run, page)
ingestion_runs  — Per-page audit log of each ingestion run: counts, status, warnings, duration
```

#### Information Classification

> **Incomplete — requires Data Steward input.**

The information processed by Tripwire is predominantly drawn from publicly available sources (legislation.gov.au, WIPO, government agency webpages). The IPFR website content being protected is also publicly accessible.

The following data elements may warrant classification review:
- Content owner and operator email addresses (stored in `tripwire_config.yaml` and passed via GitHub Actions secrets)
- LLM assessment output (structured editorial suggestions based on public content)
- Feedback replies from content owners (editorial judgements, potentially including free-text commentary)

A formal classification assessment should be conducted by the relevant Data Steward. Based on the public nature of the input data, a PROTECTED or lower classification is anticipated, but this has not been formally verified.

#### Privacy Impact

> **Incomplete — requires Data Steward input.**

Tripwire does not collect, store, or process personal information from members of the public. All monitored sources are publicly available. The only personal data handled is:
- Email addresses of the content owner and system operator (internal IP Australia staff; stored in `tripwire_config.yaml`)
- Free-text comments in feedback email replies from the content owner (internal staff member)

A Privacy Impact Assessment (PIA) may still be required under IP Australia policy. This should be confirmed with the Data Steward and Privacy Officer. The author's assessment is that the privacy risk is low given the absence of public personal data processing.

#### Change Description

This is a net-new data capability. No existing data stores are being modified or replaced. The SQLite database and all data files are created and maintained entirely by the Tripwire pipeline. The existing IPFR website is read-only (scraped); its content management system is not integrated or modified.

**Confidence summary:** The information view is comprehensively documented by the system plan (Section 9), the SQLite schema in `ingestion/db.py`, the pipeline's file I/O code, and the config YAML. *Reviewer corrections:* the table list was updated to the actual schema in `ingestion/db.py` — 10 tables, with `chunks` (not `page_chunks`) and the addition of `ingestion_runs`. Information Classification and Privacy Impact sections are honestly flagged as incomplete because they require Data Steward input that is not available in the repository. **Confidence: 8/10** (for what is described); **0/10** (for classification and PIA — not completable without Data Steward).

---

### Implementation View

```
┌──────────────────────────────────────────────────────────────────────────┐
│  GitHub (thomas-amann-ipaustralia/tripwire)                              │
│  Repository hosting, version control, state persistence                   │
│                                                                           │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  GitHub Actions (ubuntu-latest, 2-core CPU, 7 GB RAM)             │  │
│  │                                                                     │  │
│  │  ipfr_ingestion.yml     01:00 UTC daily  (60 min timeout)          │  │
│  │  tripwire.yml           02:00 UTC daily  (60 min timeout)          │  │
│  │  feedback_ingestion.yml Every 6 hours    (10 min timeout)          │  │
│  │                                                                     │  │
│  │  Python 3.11  │  PyTorch (CPU-only)  │  spaCy en_core_web_sm      │  │
│  │  Sentence-Transformers  │  rank-bm25  │  YAKE  │  trafilatura      │  │
│  │  Selenium + Chrome (for force_selenium sources)                     │  │
│  │  openai  │  mammoth  │  pyyaml  │  requests                        │  │
│  │                                                                     │  │
│  │  ~/.cache/huggingface/  (model weights, cached via actions/cache)  │  │
│  │    BAAI/bge-base-en-v1.5          ~400 MB                          │  │
│  │    Alibaba-NLP/gte-reranker-modernbert-base  ~600 MB               │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                    ↓  git commit + push                                    │
│  data/ipfr_corpus/ipfr.sqlite   (binary, committed after ingestion)       │
│  data/influencer_sources/snapshots/  (text, committed after pipeline run) │
│  data/LLM Reports/  (JSON, committed after pipeline run)                  │
│  data/logs/feedback.jsonl  (JSONL, committed after feedback ingestion)    │
└──────────────────────────────────────────────────────────────────────────┘
         ↓  GitHub Release (data sync after each run)
┌──────────────────────────────────────────────────────────────────────────┐
│  Render (cloud PaaS — dashboard hosting)                                  │
│                                                                           │
│  Node 20 / Express (server)   React / Vite (frontend)                    │
│  Persistent disk: /data (1 GB)                                            │
│  Basic Auth (DASHBOARD_USER / DASHBOARD_PASS env vars)                    │
│  ipfr.sqlite + source_registry.csv + feedback.jsonl synced from releases │
└──────────────────────────────────────────────────────────────────────────┘

External services (outbound from GitHub Actions runners):
  api.prod.legislation.gov.au  — FRL REST API (HTTPS)
  api.openai.com               — LLM assessment (HTTPS, API key)
  imap.gmail.com               — Feedback ingestion (TLS, App Password)
  smtp.gmail.com               — Notification and health alert email (TLS, App Password)
  Hugging Face CDN             — Model weight download on cache miss (HTTPS)
  ipfirstresponse.ipaustralia.gov.au — IPFR corpus scraping (HTTPS, Selenium)
  [Residential proxy]          — Optional fallback for gov.au WAF-blocked sources
```

| Component | Technology Platform | Relevant NFRs |
|---|---|---|
| Pipeline compute | GitHub Actions (ubuntu-latest) | Availability, cost, serverless |
| NLP / Semantic inference | PyTorch (CPU), Sentence-Transformers, spaCy | Performance (CPU-only, lazy load) |
| Corpus database | SQLite 3 (WAL mode) | Simplicity, Git-compatible, no server required |
| State persistence | Git repository (GitHub) | Audit trail, disaster recovery, no external storage |
| Dashboard hosting | Render (Node/Express + React) | Accessibility, Basic Auth, persistent disk |
| LLM assessment | OpenAI API (gpt-4.1-mini, configurable) | Availability, cost, structured output |
| Email delivery | Gmail / smtplib | Deliverability, App Password auth |
| Web scraping | trafilatura + Selenium + Chrome | Boilerplate removal, JS-gate bypass |

**Workflow inventory.** The repository defines four GitHub Actions workflows: three scheduled — `ipfr_ingestion.yml` (01:00 UTC daily), `tripwire.yml` (02:00 UTC daily), and `feedback_ingestion.yml` (every 6 hours) — plus one manual-only workflow, `publish-dashboard-data-release.yml` (`workflow_dispatch`), which packages `ipfr.sqlite`, `tripwire_config.yaml`, `source_registry.csv`, `feedback.jsonl`, and a snapshots tarball into a tagged GitHub Release that Render targets via its `GITHUB_RELEASE_TAG` setting. Separately, `tripwire.yml` itself publishes a timestamped `data-*` GitHub Release after every run and pings the Render deploy hook (`RENDER_DEPLOY_HOOK`), so the dashboard refreshes its data on redeploy.

> **Note:** Target State Alignment against IP Australia's Enabling Platform Roadmaps could not be assessed.

#### Change Description

Tripwire is a net-new system. There is no prior technology being decommissioned. The implementation runs entirely on external SaaS platforms (GitHub, Render, OpenAI, Google) with no on-premise infrastructure requirement.

#### Network Segmentation

Tripwire has no inbound network exposure. All traffic is outbound from ephemeral GitHub Actions runners:

- **Outbound HTTPS (port 443):** FRL API, OpenAI API, Hugging Face CDN, IPFR website, target influencer sources
- **Outbound TLS (port 587/993):** Gmail SMTP (587) and IMAP (993) for email delivery and feedback ingestion
- **Optional outbound proxy:** Residential proxy for gov.au sources that block GitHub Actions IP ranges. The proxy URL is passed as an environment variable (secret); credentials are masked in logs.
- **Dashboard (Render):** Serves over HTTPS (443); Basic Auth required for all requests; CORS locked to the assigned Render origin in production mode.

No VPN, private network, or internal network traversal is required. The system does not access any IP Australia internal systems or databases.

> **Note:** If IP Australia policy requires that the pipeline run within an IP Australia–controlled network boundary (e.g., for access to internal IPFR systems in future), this architecture would require significant redesign. This risk is noted under Risks and Issues.

**Confidence summary:** The implementation view is directly derived from the GitHub Actions workflow YAML files, `requirements.txt`, `dashboard/DEPLOY.md`, and the system plan. The technology platforms and their NFR relevance are verifiable from the code. Target State Alignment remains incomplete. **Confidence: 8/10** (for what is described).

---

### Disaster Recovery View

| Asset | Recovery Mechanism | RTO Estimate | RPO |
|---|---|---|---|
| SQLite corpus database | Restored from most recent Git commit (`git checkout HEAD -- data/ipfr_corpus/ipfr.sqlite`). Committed after every ingestion run. | Minutes (single git checkout) | 24 hours (one ingestion run's data) |
| Influencer source snapshots | Restored from Git history. 6 versions of each source snapshot are retained in the repository. | Minutes | 24 hours |
| LLM assessment reports | Stored as JSON files in `data/LLM Reports/`; committed to Git. | Minutes | 24 hours |
| Feedback log | `data/logs/feedback.jsonl` committed to Git after each feedback ingestion run. | Minutes | 6 hours |
| Configuration | `tripwire_config.yaml` is version-controlled; all parameter changes are Git-tracked. | Immediate | 0 (version-controlled) |
| Deferred LLM triggers | Stored in `deferred_triggers` table in SQLite; re-processed at the start of the next run. Triggers older than 7 days are discarded (they will be regenerated on the next pipeline run if still relevant). | Automatic on next run | 7 days maximum |

**Backup strategy:** The Git repository serves as the primary backup mechanism for all persistent data. GitHub's data durability guarantees apply. No additional backup infrastructure is required at the current scale.

**Failure scenarios and mitigations:**

| Failure | Impact | Mitigation |
|---|---|---|
| GitHub Actions runner failure mid-run | Partial run; some sources not processed | Re-run via `workflow_dispatch`; no data corruption risk (SQLite committed atomically) |
| OpenAI API unavailable | Stage 8 skipped; triggers deferred | Automatic retry at start of next run; deferred triggers table |
| Gmail SMTP failure | Email not sent | Email written to `data/logs/health_alert_<run_id>.txt`; health alert attempted via alternative path |
| SQLite corruption | Pipeline aborts with `Cannot open SQLite database` | Restore from Git: `git checkout HEAD -- data/ipfr_corpus/ipfr.sqlite` |
| GitHub Actions concurrency conflict | Second workflow waits (not cancelled) | Concurrency group `tripwire-pipeline` with `cancel-in-progress: false` prevents parallel writes |

> **Note:** Formal RTO/RPO targets have not been specified by the business owner. The estimates above reflect the technical recovery capabilities of the system as designed. The business owner should confirm whether these are adequate for operational requirements.

**Confidence summary:** All disaster recovery mechanisms are directly evidenced in the system plan (Sections 6.4, 6.5, 7.2), the GitHub Actions workflow YAML, and `pipeline.py`. The absence of formal RTO/RPO targets is flagged honestly; no invented figures have been included. **Confidence: 8/10** (for mechanisms described); **0/10** (for formal RTO/RPO — not specified in the repository).

---

### Security View

#### Secret Management

All credentials are managed as GitHub Actions encrypted repository secrets. They are never stored in version-controlled files:

| Secret | Purpose | Where Used |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API authentication | `tripwire.yml` (Stage 8) |
| `SMTP_USER` | Gmail address for outbound email | `tripwire.yml` (Stage 9) |
| `SMTP_PASSWORD` | Gmail App Password for SMTP | `tripwire.yml` (Stage 9) |
| `FEEDBACK_EMAIL` | Reply-To address in notification emails | `tripwire.yml` (Stage 9) |
| `FEEDBACK_GMAIL_USER` | Gmail address for IMAP feedback polling | `feedback_ingestion.yml` |
| `FEEDBACK_GMAIL_APP_PASSWORD` | Gmail App Password for IMAP | `feedback_ingestion.yml` |
| `SCRAPER_PROXY_URL` | Optional residential proxy (user:pass@host:port) | `tripwire.yml` (scraper fallback) |
| `RENDER_DEPLOY_HOOK` | Render deploy hook URL for dashboard refresh | `tripwire.yml` (post-run) |
| `GITHUB_TOKEN` | GitHub API access for release creation | `tripwire.yml` (data sync) |

Proxy credentials are masked in pipeline logs: only the hostname is logged (code: `proxy_url.split("@")[-1]`).

#### Application Security

- **No public-facing pipeline endpoints:** The pipeline runs as a batch job with no inbound network exposure. There is no API to attack.
- **Dashboard authentication:** The Render dashboard requires HTTP Basic Auth (`DASHBOARD_USER`, `DASHBOARD_PASS` environment variables) for all requests when `NODE_ENV=production`. CORS is restricted to the assigned Render origin.
- **Input validation:** Web-scraped content is validated before processing (minimum length, CAPTCHA detection, structural marker check, dramatic size change detection). This prevents downstream processing of malformed or adversarially crafted content.
- **SQL injection prevention:** All SQLite queries use parameterised statements (`?` placeholders); no string interpolation is used in SQL construction.
- **LLM prompt injection risk:** The LLM (Stage 8) receives content from public external sources. Prompt injection is a theoretical risk. The system prompt instructs the model to produce structured JSON only and to avoid hallucinating legal references, which limits the attack surface. The output is validated against a strict JSON schema before use.
- **Dependency supply chain:** Dependencies are pinned with minimum version constraints in `requirements.txt`. PyTorch is installed from the official CPU-only index. No dependency is sourced from unofficial channels.
- **HTTPS enforced:** All outbound connections use HTTPS or TLS (SMTP port 587, IMAP port 993). The IPFR website (force_selenium sources) is accessed over HTTPS.
- **User-Agent identification:** The pipeline identifies itself as `TripwireBot/1.0` with a link to the public repository, consistent with responsible web scraping practice.

#### Security Risk Assessment

> **Incomplete — requires IT Security Advisor and Enterprise Security Architect review.**

A formal IT Security Risk Assessment has not been conducted. The following risks are identified at the architectural level:

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Credential compromise (GitHub secrets) | Low | High | GitHub Actions secrets are encrypted at rest and only exposed to workflow runs on the configured branch; not printed in logs |
| Prompt injection via external source content | Low–Medium | Medium | LLM output validated against JSON schema; structured output mode enforced; no LLM output executed as code |
| Dependency vulnerability (supply chain) | Low–Medium | Medium | Version pinning; CPU-only PyTorch; no unofficial sources |
| Dashboard credential brute force | Low | Medium | Basic Auth; no account lockout (Render free tier limitation); mitigated by strong password requirement |
| OpenAI data retention (public sources sent to API) | Low | Low–Medium | All content sent to OpenAI is from public sources; review OpenAI's data processing terms for APS compliance |
| FRL API changes breaking Stage 3 | Medium | Medium | Fallback to webpage diff implemented; health alert on consecutive failure |

The author recommends that an IT Security Risk Assessment using IP Australia's standard template be completed before the system transitions to live operation.

**Confidence summary:** The security controls described (secret management, input validation, parameterised queries, dashboard auth, HTTPS) are all directly evidenced in the code and workflow YAML. Prompt injection risk and OpenAI data terms are identified as genuine but unresolved risks. A formal IT Security Risk Assessment cannot be completed without the IT Security Advisor and the standard risk assessment template. **Confidence: 7/10** (for controls described); **0/10** (for formal risk assessment — requires specialist input).

---

### Architecture Decisions and Patterns

| Decision | Options Considered | Decision Made | Rationale |
|---|---|---|---|
| **Compute platform** | Dedicated VM, AWS Lambda, Azure Functions, GitHub Actions | GitHub Actions | Zero marginal cost; ephemeral runners eliminate state management complexity; tight integration with version-controlled state persistence via Git commits |
| **Database technology** | PostgreSQL, MongoDB, Redis + flat files, SQLite | SQLite | Sufficient for scale (139 IPFR pages, 130 active; 156 sources); no server required; Git-compatible binary committed to repository for persistence and audit trail; WAL mode enables concurrent reads |
| **State persistence** | External object storage (S3/Azure Blob), database service, Git commits | Git commits | Provides complete audit history of all snapshots and states; no external service dependency; natural fit for a GitHub Actions–hosted system |
| **Semantic similarity approach** | BM25 only, embedding-only, cross-encoder only, bi-encoder → cross-encoder cascade | Bi-encoder (Stage 5) → cross-encoder (Stage 6) cascade with BM25 fusion | Bi-encoder is fast enough for all-pairs comparison against the full corpus; cross-encoder is reserved for the small filtered candidate set where precision matters most; BM25 adds lexical complementarity |
| **Relevance signal fusion** | Hard rules (threshold on single signal), learned ranker, RRF | Weighted Reciprocal Rank Fusion (w_bm25=1.0, w_semantic=2.0) | RRF is robust to score scale differences between signals; no training data required; higher semantic weight reflects its greater discriminative value for this domain |
| **LLM role** | LLM for all stages, LLM as sole decision-maker, LLM as final-stage verifier only | LLM as final-stage verifier (Stage 8), operating only on candidates that survived all prior gates | Limits LLM API cost to ~2–3 calls per day on typical load; reduces hallucination risk by providing strong semantic evidence (chunk scores, diff text, source metadata) in the prompt |
| **LLM model** | GPT-4o, GPT-4.1-mini, Claude, open-source (Llama) | `gpt-4.1-mini` (configurable in `tripwire_config.yaml`) | Lower cost than GPT-4o while retaining structured JSON output capability; model is configurable without code change |
| **Observation mode** | Go-live immediately, manual review before live, observation mode flag | Observation mode (`pipeline.observation_mode: true`) for 4–8 weeks before live operation | Avoids false positive storm on initial deployment; provides score distribution data for empirical threshold calibration; allows controlled sensitivity testing via manual snapshot alteration |
| **Asynchronous vs synchronous** | Async Python (asyncio), synchronous Python | Synchronous Python | Simpler to debug; predictable resource use; sequential per-source processing makes error isolation and source state management straightforward |
| **Notification method** | Webhook, ticketing system, email | Consolidated email (one per run) with structured feedback links | No external ticketing system required; email is the existing content owner workflow; mailto feedback links enable structured reply capture without a separate web form |
| **Graph propagation** | No propagation, direct edge only, multi-hop with decay | Multi-hop propagation (max 3 hops, decay 0.45 per hop, degree normalisation) | Captures legislative dependency chains (e.g., Act → Regulation → IP Australia guidance); decay prevents excessive signal diffusion; degree normalisation prevents hub-node over-activation |

**Confidence summary:** All decisions and their rationale are documented explicitly in the system plan (Section 3.4, 7.1, 7.4) or are directly verifiable from the configuration file and source code. No decisions have been inferred or speculated; all are traceable to documented artefacts. *Reviewer correction:* the scale figures in the database-technology row were updated to the live corpus (139 IPFR pages, 130 active, queried from `ipfr.sqlite`) and 156 sources. **Confidence: 9/10**

---

### Risks and Issues

> **Note:** Security risks are recorded separately under the Security View above.

#### Risks

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| R-01 | **OpenAI API model deprecation or pricing change:** the `gpt-4.1-mini` model may be deprecated or repriced, requiring a model switch. | Medium | Medium | Model is configurable via `tripwire_config.yaml` without code change. Budget monitoring recommended. | System operator |
| R-02 | **GitHub Actions runner IP blocking:** gov.au sources (notably IPFR itself and several government webpages) block GitHub Actions runner IP ranges, requiring Selenium with a residential proxy. If the proxy provider is unavailable, those sources cannot be scraped. | Medium | Medium | Selenium is the primary driver; residential proxy (`SCRAPER_PROXY_URL`) is a fallback. Sources that fail consecutively trigger health alerts. | System operator |
| R-03 | **FRL API undocumented changes:** the FRL REST API (`api.prod.legislation.gov.au`) is not formally SLA'd to external consumers. Breaking changes could disable FRL explainer document retrieval (Stage 3) for all legislative sources. | Low–Medium | High (54 FRL sources affected) | Fallback to webpage diff implemented in `stage3_diff.py`; health alerts on consecutive failure. The Attorney-General's Department should be engaged if the API is relied upon operationally. | IP Australia / Business owner |
| R-04 | **SQLite database size growth:** the Git repository approach for SQLite persistence has a practical limit of ~50 MB per file (above which Git LFS migration is required). Sustained daily ingestion over 12–24 months could breach this. | Low (short term) | Medium | System plan specifies Git LFS migration path (Section 7.2). `min_content_length: 500` and dedup thresholds help contain database growth. | System operator |
| R-05 | **Hugging Face model withdrawal:** if `BAAI/bge-base-en-v1.5` or `gte-reranker-modernbert-base` are removed from Hugging Face Hub, cached weights will eventually expire and the pipeline will fail to load models. | Very low | High | Models are open-source; the community maintains mirrors. Consider archiving model weights in a controlled location (e.g., an S3 bucket or private GitHub release) as a long-term mitigation. | System operator |
| R-06 | **Threshold miscalibration producing excessive false positives:** if the observation period is shortened or thresholds are not carefully calibrated from score distributions, the live pipeline may generate too many `CHANGE_REQUIRED` alerts, eroding content owner trust. | Medium | High | 4–8 week observation period mandated in system plan; manual snapshot alteration testing recommended; `observability.py` score distribution reports support calibration; feedback mechanism enables precision measurement. | Content owner / operator |
| R-07 | **Gmail rate limiting or App Password revocation:** Google may revoke App Passwords or rate-limit SMTP/IMAP if the sending pattern is flagged as unusual. | Low | Medium | Email failures are detected and written to fallback files; health alerts provide a secondary notification path. The Gmail approach is suitable for current volume (tens of emails/month). | System operator |
| R-08 | **Content owner email client incompatibility with mailto: feedback links:** some email clients (notably web-based clients in restricted government environments) do not honour `mailto:` links. | Low–Medium | Low | The feedback mechanism is a quality improvement tool, not a critical path. The system functions correctly without feedback. | Content owner |
| R-09 | **Multi-domain expansion outpacing governance:** as additional business units adopt the platform (anticipated in the acquisition overview), running multiple independently-configured instances could fragment configuration ownership, calibration quality, and operational responsibility if onboarding is not standardised. | Medium | Medium | Establish a new-domain onboarding runbook and configuration standards (sources, thresholds, observation-period policy, notification routing) before broad rollout; consider a shared control plane once instance count grows. | System operator / Enterprise Architect |

#### Issues

| # | Issue | Status | Description |
|---|---|---|---|
| I-01 | **Dashboard known bugs (P0):** Three P0 bugs are documented in `Backlog.md`: (BUG-001) the "View Snapshot" overlay opens empty; (BUG-002) the Topbar status pill and stage dots always appear blank; (BUG-003) the config Adjust panel cannot read or save the live config. | Open | These do not affect pipeline operation but significantly impair the dashboard's usefulness for monitoring. They are documented with root cause and definition of done in `Backlog.md`. |
| I-02 | **Deferred Phase 5 tasks blocked on production data:** threshold calibration (5.3), relevance weight grid search (5.4), internal link graph edges (5.5), and BM25 proximity extensions (5.6) require accumulated feedback data and live run history. | Blocked | These tasks cannot be started until 4–8 weeks of live operation have generated sufficient data. TODO comments reference them in the relevant source files. |
| I-03 | **Residential proxy dependency not under IP Australia control:** the `SCRAPER_PROXY_URL` is a third-party commercial service. Its availability is outside IP Australia's control. | Open | Impact is limited to the subset of `force_selenium: true` sources that are also WAF-blocked. Identify a preferred provider and document the procurement process. |
| I-04 | **`observation_mode` flag is currently `false` in `tripwire_config.yaml`:** the system is in live operation. However, `notifications.content_owner_email` and `notifications.health_alert_email` in the config still reference placeholder addresses (`content-owner@example.gov.au`, `admin@example.gov.au`). | Open | Verify that real email addresses are configured before treating any run output as authoritative. If placeholder addresses are present in the live config, notifications are being silently discarded. |

**Confidence summary:** Risks are identified from the system plan's explicit risk statements (deferred tasks, model caching, Git LFS threshold, proxy dependency) and from direct inspection of the code and workflow files. Issues I-01 and I-02 are directly documented in `Backlog.md`. Issue I-04 is identified from the actual `tripwire_config.yaml` file in the repository. No risks have been invented; all are traceable to repository artefacts. **Confidence: 8/10**

---

### Financial Impact

> **Incomplete — cost modelling requires input from the business owner and procurement team.**

The following cost categories are identifiable from the system design:

#### Implementation Costs

The system is fully implemented (all nine pipeline stages are complete and operational). Implementation was conducted outside the scope of this document; no implementation cost estimate is provided here.

#### Ongoing Operational Costs

| Cost Item | Basis | Estimated Frequency | Notes |
|---|---|---|---|
| **OpenAI API (Stage 8)** | Per-token pricing for `gpt-4.1-mini`; ~2–3 calls per run, each ~2,000 tokens (input + output) | Daily | At current OpenAI pricing, expected to be in the range of cents per day, but this should be formally estimated against the current pricing schedule and run projections. Token counts are logged in the `llm_assessments` table for empirical cost tracking. |
| **GitHub Actions minutes** | Billed above the free tier threshold; expected ~7 minutes per main pipeline run + ~5 minutes ingestion + ~2 minutes feedback ingestion per day | Daily | For a private repository, this is approximately 14 minutes/day × 30 = ~420 minutes/month. The organisation's GitHub plan determines whether this is within the free allocation. |
| **Render dashboard hosting** | Free tier (cold starts, 15 min spin-down) or Starter tier (~USD $7/month, always-on) | Monthly | Free tier is suitable for internal team use with occasional access. |
| **Residential proxy** | Per-GB or per-request pricing (provider-dependent) | Per use (only on Selenium fallback for blocked sources) | Proxy is only invoked when a direct Selenium request is WAF-blocked. Expected volume is low. |
| **System operator time** | Human effort for threshold calibration, source registry maintenance, failure response | Estimated ~2–4 hours/month (Phase 5+, after initial calibration) | Based on runbook complexity and expected alert volumes. No formal estimate is available. |

#### Savings

Tripwire replaces manual monitoring effort across 156 sources on varying check frequencies. A formal savings calculation would require the business owner to quantify:
- Current FTE time spent on source monitoring
- Time saved per alert through structured update recommendations vs. unguided review
- Reduction in content accuracy errors attributable to faster change detection

**Cost case across domains.** Because the platform is configuration-driven, the principal fixed costs (the codebase itself, GitHub Actions setup, cached model weights, the dashboard) are largely shared assets: a second or third adopting domain incurs mainly incremental variable costs (additional LLM calls, additional corpus storage, additional operator time) rather than a new build. As anticipated in the acquisition overview, broader adoption therefore improves the overall cost case by amortising the fixed investment across multiple business units. A consolidated total cost of ownership across the prospective adopting domains should be modelled by the business owner once those domains are identified.

This net present value calculation should be completed by the business owner against the cost estimates above.

**Confidence summary:** All cost line items are identifiable from the system design (OpenAI API key requirement, GitHub Actions workflow, Render deployment, proxy configuration). No specific dollar figures have been provided for items where actual prices depend on usage volumes and negotiated rates. The savings case cannot be quantified without business owner input on current manual effort. **Confidence: 4/10** (for cost category identification); **0/10** (for total cost of ownership — requires business owner and procurement input).

---

## What This Document Could Not Complete

The following sections were left incomplete because the required information does not exist in the Tripwire repository or the acquisition overview, and could only be provided by stakeholders with access to IP Australia's internal documentation or governance processes:

| Section | What Is Missing | Why |
|---|---|---|
| **Architecture Principles — Target State Alignment** (Component and Implementation Views) | Whether each component and technology platform aligns with IP Australia's Target State as defined by Business System Roadmaps and Enabling Platform Roadmaps | These documents are not in the repository |
| **Architecture Principles — Enterprise Alignment** | Formal cross-reference against IP Australia's Enterprise Architecture Principles and Enterprise NFRs | These documents are not in the repository |
| **Business Capability View — BCM Reference** | Mapping to the IP Australia Business Capability Model | Not in the repository |
| **Impacted Users — Headcount and Locations** | Number of content owners, their locations, distributed work arrangements, accessibility requirements | Not in the repository |
| **Information Classification** | Formal data classification level for all information assets | Requires Data Steward consultation |
| **Privacy Impact** | Whether a PIA is required; outcomes of PIA | Requires Data Steward and Privacy Officer consultation |
| **Security Risk Assessment** | Formal risk assessment against the IT Security Risk Assessment template | Requires IT Security Advisor and Enterprise Security Architect; template link not in repository |
| **Financial Impact — Total Cost of Ownership** | Implementation costs, precise ongoing costs, net present value calculation, savings quantification | Requires business owner input on manual effort, pricing negotiations, and organisational budget process |
| **Formal RTO / RPO targets** | Business-defined recovery time and point objectives | Not specified by the business owner; technical capabilities described but business requirements unknown |
| **Dashboard current deployment status** | Whether the Render dashboard is currently deployed and operational with real data | No evidence in the repository of a live Render deployment; placeholder email addresses in config suggest some configuration steps may be incomplete |
| **Prospective adopting business units (specifics)** | Which specific business units/domains will adopt the platform, their sources and content corpora, and adoption timing | The acquisition overview notes expressed interest in general terms only; specifics are not documented |
| **Multi-domain rollout, onboarding and governance plan** | Sequencing of domain onboarding, configuration and calibration governance, and any shared control-plane decision | Requires business owner and Enterprise Architect input; not in the repository or the acquisition overview |
