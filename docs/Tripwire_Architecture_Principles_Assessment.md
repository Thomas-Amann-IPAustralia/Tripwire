# Architecture Principles Assessment — Tripwire

### Compliance of the Tripwire Platform Against IP Australia's Enterprise Architecture Principles

**IP Australia | Internal Working Document**
**Date:** 6 July 2026
**Status:** Draft — initial compliance assessment
**Companion documents:** `docs/Tripwire_High_Level_Design.md` (system architecture, the source of the architectural facts cited below); `docs/Architecture Principles-NEW.md` (the source principles this document benchmarks against)

---

> **Why this document exists.** When the Tripwire High Level Design (HLD) was first written, IP Australia's Enterprise Architecture principles were not available to its author. The HLD's own "Architecture Principles" section therefore benchmarks Tripwire only against principles the system's design documentation states about itself (fail-closed, single source of truth for configuration, filter-funnel, modularity, minimal dependencies, synchronous processing, Git-based audit trail), and says explicitly that "alignment with any IP Australia Enterprise Architecture Principles could not be assessed, as those documents were not available to this author" — listing it under "What This Document Could Not Complete."
>
> `docs/Architecture Principles-NEW.md` has since been made available. This document closes that specific gap. It is **exclusively** an assessment of Tripwire, as designed and implemented today, against each of the 23 principles stated in that document. It does not restate Tripwire's architecture, requirements, or component inventory — see the HLD for that — except where a fact must be cited as evidence for a rating.
>
> **On redaction:** the source principles document refers to the owning organisation throughout as `[REDACTED ORGANISATION]`. Consistent with every other document in this repository, this assessment reads that placeholder as **IP Australia** and uses that name in its own analysis; it is preserved as written only in direct quotations from the source document.

---

## Methodology

Each of the 23 principles in `Architecture Principles-NEW.md` is assessed individually against the current repository: the pipeline and ingestion codebases, `tripwire_config.yaml`, the GitHub Actions workflows, the test suite, the dashboard, and the HLD. The principles are presented in the source document's own order, grouped into four categories that order implies — Business, Information, Application, Technology — a standard Enterprise Architecture grouping applied here for readability. The source document does not label these groups itself.

Each principle receives one of four ratings:

| Rating | Meaning |
|---|---|
| **Compliant** | The solution's design and implementation, as evidenced in the repository, satisfy the principle's intent. |
| **Partially Compliant** | Meaningful alignment exists, but a material gap remains that should be closed, or formally risk-accepted by an owner, before this can be called compliant. |
| **Not Compliant** | No credible evidence of alignment was found; a concrete gap exists against the principle's core intent. |
| **Not Assessable** | Compliance cannot be determined from the repository alone — it depends on an IP Australia policy, standard, or decision outside this document's reach. |

Every rating below is backed by a specific file, workflow, or code path. Every gap is stated as a gap. Where a principle cannot be fully assessed without stakeholder input (Enterprise Architect, Data Steward, Security Advisor, Legal, Business Owner), that is stated explicitly rather than assumed away — the same evidentiary discipline the HLD itself uses throughout.

---

## Compliance Scorecard

| # | Principle | Category | Rating |
|---|---|---|---|
| 1 | Business Alignment | Business | Compliant |
| 2 | Conformant | Business | Partially Compliant |
| 3 | Ownership | Business | Partially Compliant |
| 4 | Formal Services and Processes | Business | Partially Compliant |
| 5 | Consolidate and Simplify | Business | Compliant |
| 6 | Cloud First | Business | Partially Compliant |
| 7 | Early and Incremental Benefits Realisation | Business | Partially Compliant |
| 8 | Efficient Delivery of Business Outcomes | Business | Partially Compliant |
| 9 | Adaptable Solutions | Business | Compliant |
| 10 | Quality Management | Business | Partially Compliant |
| 11 | Asset | Information | Compliant |
| 12 | Shared and Reused | Information | Not Compliant |
| 13 | Available | Information | Partially Compliant |
| 14 | Single Authoritative Source | Information | Compliant |
| 15 | Common Vocabulary | Information | Partially Compliant |
| 16 | Granular and Loosely Coupled | Application | Partially Compliant |
| 17 | Continuously Integrated and Deployed | Application | Not Compliant |
| 18 | Manage Diversity | Technology | Compliant |
| 19 | Interoperable and Portable | Technology | Partially Compliant |
| 20 | Manageable and Robust | Technology | Compliant |
| 21 | Secure by Design; Secure by Default | Technology | Partially Compliant |
| 22 | Architecting for High Availability / Disaster Prevention | Technology | Partially Compliant |
| 23 | Design for Failure / DR by Design | Technology | Partially Compliant |

**7 Compliant · 14 Partially Compliant · 2 Not Compliant.** Several "Partially Compliant" ratings contain a sub-component that is genuinely not assessable without stakeholder input outside the repository; this is noted within the relevant write-up rather than broken out as a separate rating.

---

## Business Principles

### 1. Business Alignment
*"Business Solutions must deliver outcomes aligned with business objectives"*

**Rating: Compliant**

The business problem is specific, current, and well evidenced: 156 registered sources (`data/influencer_sources/source_registry.csv`) must otherwise be manually monitored for changes relevant to the IPFR website, which is time-consuming and error-prone as both the source landscape and the IPFR corpus grow (HLD, Business Problem / Opportunity). Every pipeline stage traces to a specific numbered acquisition requirement (R1–R12; HLD, Architecturally Significant Requirements), giving a direct line from business need to implementation. The platform's domain-agnostic design further aligns it with the broader organisational objective — reducing the operational cost of keeping content aligned with changing external sources — stated in the technology acquisition overview.

**Gaps / Recommendations:** None material. The acquisition overview's claim of "expressed stakeholder interest" from other business units is not yet attached to named units, timelines, or a benefit case — see Early and Incremental Benefits Realisation, below.

---

### 2. Conformant
*"Business solutions must conform to legislation, policies, regulations, directives and endorsed standards"*

**Rating: Partially Compliant**

Baseline technical conformance is evident: secrets exist exclusively as encrypted GitHub Actions secrets and never in version-controlled files (HLD, Secret Management), all outbound traffic uses HTTPS/TLS, and the pipeline identifies itself transparently to the sites it scrapes (`TripwireBot/1.0`, with a link to the public repository), consistent with responsible-scraping norms. However, three specific conformance questions remain open and are not addressed anywhere in the repository:

- **Privacy:** the HLD flags its own Privacy Impact section as incomplete pending Data Steward/Privacy Officer input; no Privacy Impact Assessment has been conducted.
- **Security policy:** no formal IT Security Risk Assessment has been performed against IP Australia's standard template (HLD, Security Risk Assessment, explicitly marked incomplete).
- **Licensing:** the repository ships a `LICENSE` file under the MIT License, with copyright asserted to an individual GitHub account ("Thomas-Amann-IPAustralia") rather than to the Commonwealth of Australia or IP Australia as an organisation. For software built to manage one of IP Australia's information assets, this is a conformance question that should be consciously decided by Legal/IP counsel, not left as a scaffolding default from initial development.

**Gaps / Recommendations:** Commission the Privacy Impact Assessment and formal Security Risk Assessment already flagged as outstanding in the HLD; confirm the licence and copyright-holder position with Legal before any wider rollout.

---

### 3. Ownership
*"[IP Australia] resources must have an owner"*

**Rating: Partially Compliant**

Ownership is asserted at the component level: the HLD's Component View table attributes every module to "IP Australia (Tripwire team)," and the two primary configuration resources — `tripwire_config.yaml` and `source_registry.csv` — are single, version-controlled files with a defined custodial mechanism: `src/config.py` loads and validates configuration at the start of every run and exits early, before any source is processed, if validation fails (`src/config.py`, module docstring).

What is missing is *named, accountable* ownership below the team level. The HLD itself notes that headcount, named roles, and locations for both the "content owner(s)" and "system operator/administrator" roles are not documented anywhere (HLD, Impacted Users). There is no `CODEOWNERS` file and no governance document, and — as noted under Conformant above — the repository's copyright is held by an individual rather than the organisation. A resource without a named, accountable owner is, per this principle's own rationale, harder to manage through its lifecycle.

**Gaps / Recommendations:** Name individuals or positions as content owner and system operator and record this in the HLD's Impacted Users section; add a `CODEOWNERS` file mapping repository paths to accountable owners.

---

### 4. Formal Services and Processes
*"Employ approved formal business services and processes for [IP Australia] solutions and service delivery"*

**Rating: Partially Compliant**

The pipeline is itself a formal, repeatable process with defined inputs, outputs, and quality gates: an 18-file `pytest` suite (`tests/`) plus a separate Vitest suite for the dashboard (`dashboard/src/tests/`), structural content validation before processing (`src/validation.py`), strict JSON-schema validation of every LLM verdict with a retry-then-defer fallback (Stage 8, per the HLD's Sequence Diagram), and weekly observability reporting (`src/observability.py`). This is materially more rigorous than the manual, ad hoc checking it replaces.

It falls short of "formal" in the enterprise sense on one count: none of this quality apparatus is *enforced*. As detailed under Continuously Integrated and Deployed (#17) below, the test suite is not run automatically anywhere, so a change could be merged and deployed without the tests that define correct behaviour ever executing. A formal process should make its own quality bar unbypassable, not merely available. Separately, IP Australia's ISO 9001:2015 certification is scoped to the Customer Service Division's core product and services; no claim is made, or should be inferred, that Tripwire falls under that certification.

**Gaps / Recommendations:** Gate merges on the existing test suite (see #17) — this converts an already-good process into an enforced one at low cost.

---

### 5. Consolidate and Simplify
*"Consolidate and Simplify for Efficiency"*

**Rating: Compliant**

This is one of the most strongly evidenced principles in this assessment. Tripwire deliberately consolidates onto a single database technology (SQLite — stated as the only permitted database technology in `CLAUDE.md`'s Constraints), a single configuration file (`tripwire_config.yaml`) in place of scattered environment variables or per-module settings, and a minimal, curated dependency set (`requirements.txt` — no web framework, no message queue, no ORM). At the platform level, the entire point of the configuration-driven, domain-agnostic core (requirement R12) is to avoid building a separate bespoke system per business unit: onboarding a new content domain is a configuration-and-data exercise, not new platform code (HLD, Component View → Transition to additional domains).

**Gaps / Recommendations:** None identified.

---

### 6. Cloud First
*"Adopt; Adapt; Buy; Build"*

**Rating: Partially Compliant**

Tripwire's infrastructure choices are strongly cloud-first: it runs entirely on commodity SaaS/PaaS — GitHub Actions for compute, OpenAI for LLM inference, Hugging Face Hub for pre-trained model weights, Render (PaaS) for the dashboard, Gmail for email — with no self-managed servers, VMs, or IaaS anywhere in the design (HLD, Implementation View; Architecture Decisions). Within cloud service selection, the design consistently prefers SaaS over PaaS over IaaS, matching the principle's own stated ordering.

Harder to evidence is the "Adopt; Adapt; Buy" half of the ordering applied to the platform's *core logic*. The nine-stage detection-and-relevance pipeline was built bespoke, and the repository contains no record of an evaluation of existing commercial change-monitoring or web-monitoring products against the adopt/adapt/buy options before that decision was made. It is entirely plausible that no COTS product combines FRL-specific legislative parsing, semantic matching against a specific content corpus, and IP Australia's email-based editorial workflow — but that reasoning, if it exists, is not recorded anywhere. The HLD's Architecture Decisions table documents *infrastructure* trade-offs (e.g. SQLite vs PostgreSQL) thoroughly, but not a build-vs-buy trade-off for the platform concept itself.

**Gaps / Recommendations:** Add a short build-vs-buy rationale to the HLD's Architecture Decisions table, even retrospectively, so the decision to build is traceable rather than assumed.

---

### 7. Early and Incremental Benefits Realisation
*"Through empirical measurements, solutions must demonstrate early and incremental benefits realisation for their business outcomes"*

**Rating: Partially Compliant**

The mechanism for early, incremental delivery is well designed: a mandated 4–8 week observation mode runs Stages 1–7 and logs scores with no LLM cost and no alerts before the system is trusted to notify anyone (`pipeline.observation_mode` in `tripwire_config.yaml`), the implementation itself proceeded in phases, and IPFR was deliberately chosen as the first and only current deployment rather than a simultaneous multi-domain rollout.

What has not yet happened is the "empirical measurement" the principle specifically requires. The HLD's own Financial Impact section rates its confidence at 4/10 for cost identification and 0/10 for savings quantification, and states plainly that a savings calculation "would require the business owner to quantify" current FTE time spent on monitoring. No baseline pre-Tripwire manual-effort measurement is recorded anywhere in the repository. Until that baseline, and a post-deployment comparison against it, exist, benefit realisation is a credible design intent rather than a demonstrated result.

**Gaps / Recommendations:** Capture a baseline manual-effort estimate now — even approximate — so a before/after comparison is possible once IPFR has completed an observation-to-live cycle.

---

### 8. Efficient Delivery of Business Outcomes
*"Business solutions must deliver business outcomes to stakeholders efficiently"*

**Rating: Partially Compliant**

Automating monitoring across 156 sources on differentiated schedules is efficient by construction relative to the manual alternative, and the HLD's Financial Impact section makes a credible efficiency case for multi-domain reuse: because the codebase, GitHub Actions setup, cached model weights, and dashboard are shared fixed assets, a second adopting business unit incurs only incremental variable cost (HLD, "Cost case across domains").

The principle also requires stakeholder involvement "through all stages of business case preparation, requirements gathering, design, delivery and testing." No such engagement record exists in the repository — the HLD's Impacted Users section explicitly notes that headcount, locations, and accessibility requirements for content owners were never captured, for IPFR or for prospective adopting units — and there is no evidence of user testing or accessibility validation for either the dashboard or the notification email format.

**Gaps / Recommendations:** Capture the stakeholder-engagement record the HLD already flags as missing (Impacted Users); this closes the gap for both this principle and #7.

---

### 9. Adaptable Solutions
*"Solutions must be able to adapt to changing business requirements and technical advances"*

**Rating: Compliant**

This is the platform's clearest design intent and its best-evidenced principle. Every dimension that varies between a monitoring deployment — sources, the target content corpus, monitoring schedules, notification routing — is configuration or data, not code (`source_registry.csv`, `tripwire_config.yaml`, the ingested corpus database; requirement R12; `CLAUDE.md`'s explicit "forkable" framing). Extending to a new content domain requires no platform-code change (HLD, Component View → Transition to additional domains). The LLM model itself is swappable via a single configuration field (`pipeline.llm_model`) without a code change, directly evidencing adaptability to technical advances such as a newer or cheaper model becoming available.

**Gaps / Recommendations:** The dashboard's responsiveness and accessibility — the principle's own worked example of "adaptable" delivery — is not evidenced either way in the repository; worth a quick accessibility pass given the principle names it explicitly.

---

### 10. Quality Management
*"Solutions must meet quality management objectives"*

**Rating: Partially Compliant**

The engineering quality bar is genuinely high: 18 pytest files plus a dashboard Vitest suite, structural content validation (`src/validation.py`) guarding against malformed or CAPTCHA-gated scrapes before they enter the pipeline, strict JSON-schema validation of every LLM verdict with a retry-then-defer fallback, and both weekly observability reporting and post-run health alerting to catch quality regressions in production (`src/observability.py`, `src/health.py`).

As under Formal Services and Processes and Continuously Integrated and Deployed, this apparatus is not automatically enforced — nothing currently stops a change from being merged and running in production without the test suite executing against it. No formal Quality Management System certification is claimed for Tripwire, and none is implied here; IP Australia's ISO 9001:2015 certification applies to the Customer Service Division's core product and services, a different scope. The gap is about closing the loop between "tests exist" and "tests must pass," not about certification.

**Gaps / Recommendations:** Same as #17 — gate merges on the test suite.

---

## Information Principles

### 11. Asset
*"Information is managed as an asset"*

**Rating: Compliant**

The IPFR corpus is actively lifecycle-managed rather than treated as a disposable cache: pages carry an explicit status (`active` / `stub` / `duplicate`; HLD, SQLite Schema Summary), the ingestion pipeline performs exact and near-duplicate detection and IDF-based filtering (`ingestion/dedup.py`) to keep the corpus from accumulating redundant or ambiguous information, and every state change — snapshots, the SQLite database, LLM reports — is committed to Git, giving the asset a complete, auditable history of what was known and when (HLD, Disaster Recovery View).

**Gaps / Recommendations:** None at the technical level. Formal information classification remains outstanding per the HLD (Information Classification, confidence 0/10) — an information asset should carry a classification before it is fully "managed" in the Commonwealth information-management sense.

---

### 12. Shared and Reused
*"Information is collected once and used for as many business operations as are required"*

**Rating: Not Compliant**

This is the most significant tension this assessment found. Tripwire produces genuinely valuable derived information — cleaned page content, chunk-level embeddings, named entities, YAKE keyphrases, and a quasi-graph of content relationships (`ingestion/enrich.py`, `ingestion/graph.py`) — but none of it is exposed for reuse beyond Tripwire's own pipeline. There is no API, export format, or documented access path that would let another IP Australia system or business unit query this corpus; the only outputs that leave the system are the consolidated notification email and the Basic-Auth-protected dashboard, both aimed at Tripwire's own content-owner/operator audience.

More significantly, the platform's multi-domain extension model works against this principle rather than toward it. Onboarding a second business unit means standing up an independently-configured instance with its own corpus database: the HLD's Out of Scope section states that multi-domain use is achieved by deploying additional, independently-configured instances, each with its own source registry, corpus, and notification routing, and its Component View confirms each domain runs as its own configured instance with its own corpus database. That is a defensible engineering choice for isolation and blast-radius control, and it is exactly what makes the platform so cleanly *adaptable* (#9). But it means entity extraction, embedding generation, or an overlapping source landscape — many sources, such as FRL Acts, are plausibly relevant to more than one content domain — would be re-collected and re-processed per domain rather than collected once and reused, the opposite of this principle's intent.

**Gaps / Recommendations:** This is a genuine architectural trade-off, not a defect, and should be surfaced to the Enterprise Architect explicitly rather than silently accepted as domains are added. The HLD already identifies a shared multi-tenant control plane as a candidate future component (HLD, Risk R-09); this principle is the specific reason that candidate exists. If a second domain is onboarded, evaluate whether cross-domain source overlap and enrichment (embeddings, NER, keyphrases) justify a shared corpus/enrichment layer serving multiple per-domain relevance configurations, versus the current fully-isolated-instance model.

---

### 13. Available
*"Information is available when needed... discoverable... accessible"*

**Rating: Partially Compliant**

Within its intended audience, information is available: the consolidated email reaches the content owner every run with the recommendation and its supporting evidence, and the Render dashboard exposes run history, source health, and alert counts behind Basic Auth (HLD, Component View; Security View). Health alerts proactively notify the operator rather than requiring them to go looking for problems.

Availability is narrow rather than broad — appropriate for the current single-domain deployment, but worth naming. There is no discovery mechanism by which anyone outside the small operator/content-owner audience could learn this information exists at all (the "discoverability" half of this principle, as distinct from accessibility), and the dashboard's free-tier Render hosting is explicitly subject to cold-start spin-down (HLD, Financial Impact), giving "available when needed" a real, if minor, latency caveat on the dashboard specifically. The underlying SQLite file, being Git-committed, is technically reachable by anyone with repository access — but that is an artefact of the persistence mechanism (see Single Authoritative Source) rather than a designed access or discovery path.

**Gaps / Recommendations:** None urgent at current scale; revisit if other business units need to discover what Tripwire already knows — the discoverability half of the Shared and Reused gap above.

---

### 14. Single Authoritative Source
*"Information items must have a single identified authoritative source"*

**Rating: Compliant**

The platform is internally disciplined here. `tripwire_config.yaml` is the sole, version-controlled source of truth for every tuneable parameter — its own header states this directly, noting the file "is version-controlled so every parameter change is tracked as a commit" (`tripwire_config.yaml`, header comment) — and it is validated at the start of every run, failing closed before any source is processed if invalid (`src/config.py`). `source_registry.csv` plays the same role for the monitored-source list. Neither is duplicated elsewhere; `CLAUDE.md` states explicitly that there are "No env vars for config."

One distinction is worth making explicit rather than leaving implicit, since this principle is precisely about knowing which copy is authoritative: `data/ipfr_corpus/ipfr.sqlite` is **not** the authoritative source of IPFR content — it is a daily-refreshed derived cache used for semantic matching. The live `ipfirstresponse.ipaustralia.gov.au` website remains authoritative, and Tripwire's copy can be up to 24 hours stale by design (HLD, Disaster Recovery View — a 24-hour RPO on the corpus). The system behaves correctly on this point today, but the distinction is nowhere stated as a governance rule, which will matter more as soon as a second consumer of the SQLite file exists (see #12).

**Gaps / Recommendations:** State explicitly, e.g. in the HLD's Information View, that the SQLite corpus is a derived/cached copy and not the authoritative source of IPFR content — a one-line governance note that pre-empts future confusion.

---

### 15. Common Vocabulary
*"Information must be defined consistently and completely... understandable and available to all users"*

**Rating: Partially Compliant** (internal vocabulary is consistent; enterprise-wide alignment is **Not Assessable**)

Internally, Tripwire's vocabulary is consistent and documented: the SQLite schema (`pages`, `chunks`, `entities`, `keyphrases`, `graph_edges`, `sections`, `pipeline_runs`, `deferred_triggers`, `llm_assessments`, `ingestion_runs`) is defined once in `ingestion/db.py` and described identically in `CLAUDE.md` and the HLD's Information View, and pipeline-specific terms (trigger, verdict, significance fingerprint, TriggerBundle) are used consistently across code, tests, and documentation.

Whether this vocabulary reconciles with any IP Australia enterprise-wide data dictionary cannot be assessed from the repository — no such dictionary is present to check against, the same limitation the HLD records for the Business Capability Model. Terms like "content asset," "source," and "content owner" are plain English within Tripwire; whether they map onto formally defined enterprise terms is a question for the Enterprise Architect, not one this assessment can resolve.

**Gaps / Recommendations:** When an enterprise data dictionary becomes available, reconcile Tripwire's schema vocabulary against it as a discrete follow-up task.

---

## Application Principles

### 16. Granular and Loosely Coupled
*"Business solution components to be Granular and Loosely coupled... APIs as the preferred mechanism for solution component integration"*

**Rating: Partially Compliant**

Granularity is genuinely strong: the pipeline is decomposed into one module per responsibility (`stage1_metadata.py` through `stage9_notification.py`, each independently testable per `CLAUDE.md`'s Repository Structure), the ingestion pipeline is a wholly separate package (`ingestion/`) from the monitoring pipeline (`src/`), and the dashboard's own Express backend is itself split into per-resource route modules (`dashboard/server/routes/`: `config`, `embeddings`, `feedback`, `graph`, `health`, `llm-reports`, `pages`, `runs`, `snapshots`, `sources`, `sql`).

Looser coupling *within* the pipeline is real — each stage is a gate the previous stage's output must pass through — but the principle specifically names APIs as "the preferred mechanism for solution component integration and service consumption," and that is not how Tripwire's components integrate. The nine stages are wired together by direct in-process function calls from a single orchestrator (`pipeline.py`), not by service calls. The dashboard does have a real internal REST API (the routes listed above, behind Basic Auth), but it is unpublished and undocumented — no OpenAPI/Swagger specification exists anywhere in the repository — and is not intended for consumption by anything other than its own React frontend. No component of Tripwire currently exposes itself as an API another IP Australia system could integrate against.

**Gaps / Recommendations:** For a single-purpose batch pipeline this is a pragmatic, defensible choice rather than an oversight — but it deserves a conscious decision rather than a default. If #12 is ever acted on, an API is the natural mechanism this principle itself recommends for exposing Tripwire's corpus or output to other consumers.

---

### 17. Continuously Integrated and Deployed
*"Fully automated releases... rely on automated testing, containerisation and deployment tools"*

**Rating: Not Compliant**

This is the clearest, most concrete gap in this assessment. The repository defines exactly four GitHub Actions workflows (`ipfr_ingestion.yml`, `tripwire.yml`, `feedback_ingestion.yml`, `publish-dashboard-data-release.yml`), and all four are operational/scheduled data-pipeline runs, not build-test-deploy automation for the codebase itself. None of the four executes `pytest` or `vitest` — verified directly against all four workflow files — and there is no separate CI workflow either. This means the 18-file pytest suite and the dashboard's Vitest suite, both genuinely strong test assets, are not run automatically on any push or pull request. A change could be merged and would run in production on the next scheduled cycle without the test suite that defines correct behaviour ever executing against it. (GitHub branch-protection rules are a repository setting outside the codebase and cannot be verified from the file system; even if one were configured, there is currently no CI workflow for such a rule to require.)

The principle also names containerisation specifically; there is no `Dockerfile` or container configuration anywhere in the repository. Given the platform runs on `ubuntu-latest` GitHub Actions runners with pip-installed dependencies, this is a lower-severity gap than the missing test automation, but it means environment reproducibility rests entirely on `requirements.txt`'s minimum-version constraints (`>=`, not pinned exact versions or a lockfile) rather than a reproducible image.

**Gaps / Recommendations:** Add a CI workflow that runs `pytest tests/ -v` and the dashboard's `npm run test` on every pull request, and require it to pass before merge. This is the single highest-value, lowest-cost recommendation in this assessment: the quality infrastructure already exists (see #4, #10) — it simply is not wired in.

---

## Technology Principles

### 18. Manage Diversity
*"Technology diversity must be controlled"*

**Rating: Compliant**

The monitoring pipeline commits to a single language (Python 3.11+), a single database technology (SQLite — stated as the only permitted database technology in `CLAUDE.md`'s Constraints), and a minimal, deliberately curated dependency set (`requirements.txt` has no web framework, no message broker, no ORM). The dashboard introduces a second stack (Node 20/Express, React/Vite), a reasonable and bounded instance of diversity — a browser-facing UI is a genuinely different problem from a batch NLP pipeline — rather than uncontrolled sprawl. No evidence of redundant or competing technology choices (e.g. two different HTTP client libraries or templating approaches) was found in either stack.

**Gaps / Recommendations:** None identified.

---

### 19. Interoperable and Portable
*"Technology should conform to defined standards that promote interoperability and portability"*

**Rating: Partially Compliant**

Data portability is genuinely good: every persistent artefact uses an open, non-proprietary format — YAML configuration, CSV registries, JSON reports, SQLite (an open, single-file, widely-supported format with no server lock-in). Moving the entire system to a different compute provider would mean moving files, not migrating a proprietary database.

Interoperability *with other systems*, as distinct from portability of the platform's own files, is essentially absent by design: the HLD lists replacement or modification of any content management system as explicitly out of scope, and — as covered under #16 — no API is published for other systems to consume. Tripwire currently interoperates with exactly one external actor: a human, via email. That is a reasonable scope decision for the current single-domain deployment, and the principle's rationale — enabling "the rapid integration of processes, systems and data" — is simply not yet a requirement Tripwire has been asked to meet. It is a limitation worth naming plainly rather than leaving implicit.

**Gaps / Recommendations:** None urgent; revisit alongside #12 and #16 if multi-domain or cross-system integration becomes a real requirement.

---

### 20. Manageable and Robust
*"Technology to be manageable and robust"*

**Rating: Compliant**

Manageability is well evidenced: `src/health.py` evaluates post-run conditions and alerts the operator, `src/observability.py` produces weekly score-distribution reports, every run is logged to the `pipeline_runs` table, and the dashboard surfaces run history and source health for administration (HLD, Component View). Robustness patterns are consistent throughout: per-source failure isolation so that one bad source among 156 cannot block the rest (`CLAUDE.md`'s fail-closed principle; `retry.py`'s exponential backoff), deferred-trigger storage and retry when the LLM API is unavailable, and fallback-file writes when SMTP delivery fails (HLD, Disaster Recovery View → Failure scenarios and mitigations).

The one caveat is scope, not execution: the principle also asks that technology "take advantage of the inherent resilience available in cloud computing to satisfy high availability... requirements," and Tripwire's dependencies (GitHub Actions, OpenAI, Gmail, Render) are each single-provider with no failover. For a daily batch pipeline with no real-time customer-facing obligation this is very likely proportionate, but — as the HLD itself notes — no business owner has yet formally confirmed that this level of availability is sufficient. See #22.

**Gaps / Recommendations:** None on manageability. On robustness/availability, obtain the business-owner sign-off the HLD already flags as outstanding.

---

### 21. Secure by Design; Secure by Default
*"Appropriate measures are in place to secure [IP Australia]'s people, systems, data, information and assets by design and default"*

**Rating: Partially Compliant**

The baseline security posture is solid and verifiable in code: every credential is a GitHub Actions encrypted secret and none appear in version-controlled files (HLD, Secret Management); all SQLite access uses parameterised queries with no string-interpolated SQL (HLD, Application Security); scraped content is validated — length, CAPTCHA detection, structural markers, size-ratio checks (`src/validation.py`) — before it enters the pipeline; the dashboard requires Basic Auth in production with CORS locked to its own origin; and proxy credentials are masked in logs.

The HLD already identifies, and this assessment confirms, that none of this has yet been through independent verification: no formal IT Security Risk Assessment has been conducted (HLD, Security Risk Assessment, explicitly marked incomplete), the dashboard has no account lockout or MFA (a Render free-tier limitation the HLD records as a residual risk), and the mitigations for LLM prompt-injection (structured-output enforcement, schema validation) are reasoned but untested by anyone independent of the system's own author. "Secure by design" is well argued here; "secure by default," in the sense of independently verified, is not yet established.

**Gaps / Recommendations:** Commission the IT Security Risk Assessment the HLD already recommends, using IP Australia's standard template, before any additional business unit is onboarded.

---

### 22. Architecting for High Availability / Disaster Prevention
*"Controls and measures... assure systems and applications are robust and fault tolerant against a range of scenarios as agreed with business"*

**Rating: Partially Compliant**

Disaster-prevention thinking is genuinely present at the application layer: the pipeline isolates failures per source so a single broken source cannot cascade into a full outage, the concurrency group on `tripwire.yml` prevents two overlapping runs from corrupting the SQLite file (`cancel-in-progress: false`, per HLD, Disaster Recovery View), and every category of external dependency failure — LLM unavailable, SMTP unavailable, database corruption — has a documented, working mitigation.

What has not happened is the "as agreed with business" half of the principle's own wording. There is no failover between compute, LLM, or email providers: if GitHub Actions, OpenAI, or Gmail individually fail, the corresponding function of Tripwire stops until that single provider recovers — graceful degradation, not redundancy. That may well be an entirely acceptable risk position for a daily, non-customer-facing batch system, but it is a position that has not been explicitly put to, and agreed by, a business owner; today it exists by default rather than by decision.

**Gaps / Recommendations:** Put the current single-provider-per-dependency posture to the business owner as an explicit, documented risk acceptance (or a decision to add redundancy) rather than leaving it an implicit default.

---

### 23. Design for Failure / DR by Design
*"Appropriate measures are in place to assure recovery within BCP (RTO and RPO) requirements"*

**Rating: Partially Compliant**

This principle is, mechanically, one of the better-covered areas of the whole system: the HLD's Disaster Recovery View gives an asset-by-asset recovery mechanism, RTO estimate, and RPO for every persistent store (SQLite corpus, snapshots, LLM reports, feedback log, configuration, deferred triggers), all backed by Git, with recovery generally achievable in minutes via `git checkout` (HLD, Disaster Recovery View).

The principle's core requirement, however, is recovery "within BCP (RTO and RPO) requirements" — i.e. against a business-set target — and the HLD is explicit that no such target has ever been set: formal RTO/RPO targets have not been specified by the business owner, and the documented estimates reflect only the system's technical recovery capability as designed. The mechanisms are demonstrably fast (minutes, for a system with a 24-hour data-freshness cycle in any case), so any reasonable business target is likely to be met — but "likely" is not the same as "verified against an agreed target," which is what this principle actually asks for.

**Gaps / Recommendations:** Ask the business owner to set formal RTO/RPO targets, as the HLD already recommends; given the demonstrated minutes-scale recovery capability, this should be a quick confirmation rather than a redesign.

---

## Cross-Cutting Findings

Read individually, the 23 principles produce a scattered list of gaps. Read together, they cluster into four recurring themes:

1. **Strong engineering discipline, weak enforcement.** The test suite (#4, #10), the security controls (#21), and the disaster-recovery mechanisms (#23) are all genuinely well built, and none of them is backed by an automated gate that guarantees they stay that way. The single highest-leverage fix in this assessment is wiring the existing test suite into CI (#17); it would materially improve the rating of three separate principles (#4, #10, #17) for roughly one afternoon of workflow-file work.

2. **Governance artefacts consistently trail the technology.** Named ownership (#3), a licensing decision (#2), a Security Risk Assessment (#2, #21), a Privacy Impact Assessment (#2), and business-agreed RTO/RPO targets (#23) are all outstanding — and all were already flagged as outstanding in the HLD before this assessment began. This document does not surface new organisational gaps so much as confirm, from a different angle, the same list the HLD's authors were already honest about.

3. **The platform is built to be *adaptable* (replicated per domain) rather than *shared* (used once across domains).** Principle 9 (Adaptable Solutions) and principle 12 (Shared and Reused) pull in opposite directions, and Tripwire's design has clearly and consistently chosen the former: one independently-configured instance per content domain, each with its own corpus. That is a reasonable, even good, choice for a single-domain deployment, but it deserves the Enterprise Architect's attention now, while there is only one instance, rather than after several business units have each stood up an isolated copy of the enrichment pipeline against what may be an overlapping source landscape.

4. **Interoperability is deliberately minimal, and that is a scope decision, not an omission.** Principles #16 and #19 both note the absence of any published API or system-to-system integration. Given the explicit out-of-scope statement on CMS integration and the current single-domain deployment, this is consistent with the platform's mandate today — but it should remain a conscious decision as multi-domain adoption is considered, not a default that goes unexamined.

---

## Consolidated Recommendations

| Priority | Recommendation | Principles Addressed |
|---|---|---|
| High | Add a CI workflow that runs the existing `pytest` and `vitest` suites on every pull request and blocks merge on failure | #4, #10, #17 |
| High | Commission the IT Security Risk Assessment and Privacy Impact Assessment already flagged as outstanding in the HLD | #2, #21 |
| Medium | Name individuals or positions as content owner and system operator; add a `CODEOWNERS` file | #3 |
| Medium | Confirm the MIT licence and individual copyright-holder position with Legal/IP counsel | #2, #3 |
| Medium | Obtain business-owner sign-off on formal RTO/RPO targets and on the single-provider-per-dependency availability posture | #22, #23 |
| Medium | Capture a baseline manual-effort estimate now, to support a future before/after benefit measurement | #7 |
| Low | Document, as an explicit governance note, that the SQLite corpus is a derived cache and the live website remains authoritative | #14 |
| Low | Record a short build-vs-buy rationale for building the pipeline bespoke rather than adopting a COTS product | #6 |
| Low | Raise the Adaptable-vs-Shared tension with the Enterprise Architect before a second business unit is onboarded | #12 |
| Low | Add an accessibility pass for the dashboard and the notification email format | #8, #9 |

---

## What This Assessment Could Not Complete

Consistent with the HLD's own practice, the following are stated as open rather than assumed:

| Item | Blocks Full Assessment Of | Requires |
|---|---|---|
| Enterprise data dictionary / common vocabulary standard | #15 (enterprise-wide half) | Enterprise Architect |
| IP Australia Business Capability Model | Indirectly informs #1, #8 (already flagged in the HLD) | Enterprise Architect |
| Formal IT Security Risk Assessment | #21 | IT Security Advisor / Enterprise Security Architect |
| Privacy Impact Assessment | #2 | Data Steward / Privacy Officer |
| Information classification | #2, #11 | Data Steward |
| Business-agreed RTO/RPO targets | #22, #23 | Business Owner |
| Named ownership / headcount for content-owner and operator roles | #3, #8 | Business Owner |
| Legal/IP position on MIT licence and copyright holder | #2, #3 | Legal / IP Counsel |
| Specifics of prospective adopting business units | #12 cross-domain evaluation | Business Owner / Enterprise Architect |

---

**Overall confidence:** Ratings for principles assessable directly from the repository — the technical and process principles, the large majority — are made with high confidence, each backed by a specific file, workflow, or code citation. Ratings that depend in part on organisational context outside the repository (Conformant, Ownership, the stakeholder-interest claim under Business Alignment, the enterprise half of Common Vocabulary) are marked accordingly and should be read as provisional pending the stakeholder input listed above. No rating in this document asserts an organisational fact not evidenced in the repository, the HLD, or the acquisition overview.
