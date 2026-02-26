# Tripwire

## Objective

**Tripwire must maximize recall of plausible downstream impacts and minimize LLM prompt cost through evidence filtering, batching, and structured payloads. Final impact confirmation is to be performed by the LLM.**

Autonomous monitoring system for tracking substantive changes in authoritative Intellectual Property sources—such as Australian legislation and WIPO feeds—to detect updates that may impact IP First Response (IPFR) content. 

Tripwire is a recall‑first early warning system.

It asks:
**What might be impacted?**

The LLM answers:
**What is actually impacted?**

---

## System Overview

Tripwire operates as a staged pipeline:

```
Stage 0 → Source metadata detection
Stage 1 → Content normalization & archive management
Stage 2 → Diff generation
Stage 3 → Semantic impact estimation & routing decision
Stage 4 → LLM confirmation
```

Tripwire does not determine truth.  Tripwire determines what deserves LLM attention.

---

## Architecture Diagram

![Tripwire Semantic Monitoring Workflow](an_infographic_style_flowchart_titled_tripwire_se.png)

---

## Stage Logic Summary

### Stage 0 – Version Detection

Sources are probed using lightweight metadata:

- Legislation → registerId
- WebPage / RSS → ETag / Content-Length

Purpose:

- Avoid unnecessary downloads  
- Detect objective source changes  
- Preserve auditability  

---

### Stage 1 – Content Normalization

Changed sources are fetched and cleaned:

- Remove navigation & layout artifacts  
- Normalize into Markdown / stable XML  

Purpose:

- Reduce diff volatility  
- Prevent false semantic triggers  

---

### Stage 2 – Diff Generation

Unified diffs are generated against archived content.

Tripwire reasons over changes, not full documents.

---

### Stage 3 – Semantic Impact Estimation

Diffs are parsed into semantic hunks.

Noise suppression removes:

- Page numbers  
- Standalone dates  
- Trivial fragments  

Substantive hunks are:

1. Embedded  
2. Compared against semantic chunk corpus  
3. Aggregated into page-level candidates  

Scoring model:

```
final_score =
    base_similarity
  + coverage_bonus
  + density_bonus
  + power_word_uplift
```

---
## Tiered Processing Scenarios

| Priority | Strategy | Rationale | Workflow Detail |
| :--- | :--- | :--- | :--- |
| **High** | **Maximum Recall** | Never suppress high-risk sources. | **1. Summarize:** Detail the update immediately.<br>**2. Identify:** Map to all potentially influenced IPFR content.<br>**3. Verify:** Confirm actual influence with zero noise filtering. |
| **Medium** | **Balanced Filter** | Balance recall & cost. | **1. Filter:** Remove minor noise (formatting/boilerplate).<br>**2. Summarize:** Extract substantive changes.<br>**3. Map & Verify:** Identify and confirm content influence. |
| **Low** | **Efficiency First** | Suppress low-impact chatter. | **1. Extensive Filter:** Isolate only major textual or legal shifts.<br>**2. Summarize:** Brief overview of the core change.<br>**3. Map & Verify:** Identify and confirm impact only if thresholds are met. |

### Example
```mermaid
graph TD
    %% Node Definitions
    A[Detected Change] --> B{Check Priority}

    %% High Priority Path
    B -- High Priority: Trade Marks Act --> C[Calculate Cosine Similarity]
    C --> D{Score > 0.65?}
    D -- Yes: 0.68 --> E[GENERATE HANDOVER PACKET]
    E --> F[LLM Impact Analysis]

    %% Low Priority Path
    B -- Low Priority: WIPO Press Room --> G[Calculate Cosine Similarity]
    G --> H{Score > 0.85?}
    H -- No: 0.72 --> I[SUPPRESS CHANGE]
    I --> J[Log to Audit: No Impact]

    %% Styling
    style E fill:#d4edda,stroke:#28a745,stroke-width:2px
    style I fill:#f8d7da,stroke:#dc3545,stroke-width:2px
    style F fill:#fff3cd,stroke:#ffc107,stroke-width:2px
```

## LLM Handover Design

When Stage 3 triggers handover:

- Candidates ≥ candidate_min_score retained  
- No truncation of qualifying candidates  
- Batched via MAX_CANDIDATES_PER_PACKET  
- Structured JSON payloads generated  

Purpose:

- Minimize prompt tokens  
- Preserve evidence traceability  
- Enable deterministic batching  

---

## LLM Responsibilities (Downstream)

Curently, Tripwire provides hypotheses + evidence.

The LLM will need to perform:

- Final impact confirmation  
- Change interpretation  
- Suggested updates  

Important notes for LLM interpretation:

- Scores are probabilistic signals  
- Thresholds are cost controls  
- Power words influence ranking, not truth  
- Multiple pages may legitimately be impacted

---

## Logs & Artifacts

| File | Role |
|------|------|
| audit_log.csv | Source/version ledger |
| llm_handover_log.csv | Semantic routing decisions |
| diff_archive/*.diff | Change evidence |
| handover_packets/*.json | LLM payloads |

---

