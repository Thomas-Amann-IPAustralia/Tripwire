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
Stage 0 → Source metadata detection (ETag/registerId)
Stage 1 → Content normalization (Cleaning & Markdown conversion)
Stage 2 → Diff generation (Unified diff vs. Archive)
Stage 3 → Semantic impact estimation & Routing
Stage 4 → Two-Pass LLM Verification & Review Scoping
```

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

#### Scoring and handover policy:
The `page_final_score` is calculated using a base similarity with additive bonuses:
* **Base Similarity**: The maximum chunk_similarity observed for a page.
* **Coverage Bonus**: +0.04 per unique hunk matched (capped at +0.12).
* **Density Bonus**: +0.01 per additional chunk hit (capped at +0.06).
* **Power Word Uplift**: Boosts based on legal imperatives (e.g., "must", "penalty", "Archives Act").

```
page_final_score =
    page_base_similarity # max chunk_similarity observed for a page before bonuses/uplift
  + coverage_bonus # which unique hunks contributed matches, captured with matched_hunks
  + density_bonus # how many passing chunk matches hit this page, captured with chunk_hits
  + power_word_uplift
```

#### Routing Thresholds
When Stage 3 triggers handover:

- Candidates ≥ candidate_min_score retained  
- No truncation of qualifying candidates  
- Batched via MAX_CANDIDATES_PER_PACKET  
- Structured JSON payloads generated  

Purpose:

- Minimize prompt tokens  
- Preserve evidence traceability  
- Enable deterministic batching
  
Handover is triggered based on source priority and the `CANDIDATE_MIN_SCORE` (0.35):

| Priority | Handover Trigger | Threshold Type |
| :--- | :--- | :--- |
| **High** | Any candidate ≥ 0.35 | Maximum Recall |
| **Medium** | Primary Score ≥ 0.45 | Balanced Filter |
| **Low** | Primary Score ≥ 0.50 | Efficiency First |


### Stage 4 - Two-Pass LLM Verification
Stage 4 executes a deterministic verification workflow to ensure high-fidelity results while minimizing token usage:

### Pass 1: Impact Confirmation
* **Input**: A "Local Evidence Window" from the IPFR markdown archive including Before, Current, and After context.
* **Task**: Verify if the external change materially impacts the specific IPFR section.
* **Output**: A structured decision (`impact`, `no_impact`, or `uncertain`) with grounded reasoning and evidence quotes.

### Pass 2: Review Scoping
* **Input**: A compact "Chunk Index" (IDs + snippets) of the entire candidate page.
* **Task**: Identify all other sections of the page that require human review based on the confirmed impact.
* **Output**: A list of `confirmed_update_chunk_ids` and `additional_chunks_to_review` to seed the human editing workflow.

---
## Stage 3 Tiered Processing Scenarios

| Priority | Strategy | Rationale | Workflow Detail |
| :--- | :--- | :--- | :--- |
| **High** | **Maximum Recall** | Never suppress high-risk sources. | **1. Summarize:** Detail the update immediately.<br>**2. Identify:** Map to all potentially influenced IPFR content.<br>**3. Verify:** Confirm actual influence with zero noise filtering. |
| **Medium** | **Balanced Filter** | Balance recall & cost. | **1. Filter:** Remove minor noise (formatting/boilerplate).<br>**2. Summarize:** Extract substantive changes.<br>**3. Map & Verify:** Identify and confirm content influence. |
| **Low** | **Efficiency First** | Suppress low-impact chatter. | **1. Extensive Filter:** Isolate only major textual or legal shifts.<br>**2. Summarize:** Brief overview of the core change.<br>**3. Map & Verify:** Identify and confirm impact only if thresholds are met. |

### Example
```mermaid
flowchart LR
    A[Monitored sources] --> B[Stage 0<br/>Version detection]
    B --> C[Stage 1<br/>Fetch + normalise]
    C --> D[Stage 2<br/>Diff generation]
    D --> E[Stage 3<br/>Semantic retrieval]
    E --> F[Handover packets]
    F --> G[Stage 4<br/>LLM verification]
    G --> H[Stage 5<br/>LLM update suggestions]
    H --> I[Review queue]
    I --> J[Monitoring outputs]

    J --> J1[audit_log.csv]
    J --> J2[diff artifacts]
    J --> J3[verification JSON]
    J --> J4[update suggestion JSON]
    J --> J5[update_review_queue.csv]
    J --> J6[GitHub summary]

    classDef stage0 fill:#E8F1FB,stroke:#4A90E2,color:#0B2545,stroke-width:2px;
    classDef stage1 fill:#EAF7EE,stroke:#34A853,color:#123524,stroke-width:2px;
    classDef stage2 fill:#FFF4E5,stroke:#FB8C00,color:#5D3200,stroke-width:2px;
    classDef stage3 fill:#F3E8FF,stroke:#8E44AD,color:#3D1A52,stroke-width:2px;
    classDef stage4 fill:#FCE8E6,stroke:#DB4437,color:#5C1F1A,stroke-width:2px;
    classDef stage5 fill:#E6F4EA,stroke:#188038,color:#16351F,stroke-width:2px;
    classDef output fill:#EEF3F7,stroke:#7B8A97,color:#23313F,stroke-width:1.5px;

    class B stage0;
    class C stage1;
    class D stage2;
    class E,F stage3;
    class G stage4;
    class H,I stage5;
    class J,J1,J2,J3,J4,J5,J6 output;
```

```mermaid
graph TD
    subgraph Stage0 [Stage 0, 1 & 2: Detection and Change Generation]
        A[fetch_stage0_metadata] --> B{Version Changed?}
        B -- No --> C[Log Success: No Change]
        B -- Yes --> D[download_legislation_content / fetch_webpage_content]
        D --> E[get_diff: Generate .diff Hunks]
    end

    subgraph Stage3 [Stage 3: Semantic Routing]
        E --> F[calculate_similarity]
        F --> G[detect_power_words: Must/Shall/Penalty]
        G --> H[Vector Search: Hunk vs. Semantic_Embeddings_Output.json]
        H --> I{should_handover?}
        I -- No --> J[Log Filtered in audit_log.csv]
        I -- Yes --> K[generate_handover_packets: JSON to handover_packets/]
    end

    subgraph Stage4 [Stage 4: Two-Pass LLM Verification]
        J --> K[Pass 1: Verify Page Impact]
        K -- "No Impact / Uncertain" --> K_Exit[Log to Audit: No Action]
        K -- "Impact Confirmed" --> L[Pass 2: Adjudicate Chunks: AI acts as a high-precision judge to decide which specific parts of an IPFR page actually need to be changed.]
        L --> O[Save result to llm_verification_results]
    end

    subgraph Stage5 [Stage 5: Content Drafting]
        O --> P[run_llm_update_suggestions: LLM Content Update]
        P --> Q[Draft Proposed Replacement Text for confirmed chunks]
        Q --> R[write_update_review_queue_csv: Human Review Queue]
    end

    %% Styles
    style Stage0 fill:#f9f9f9,stroke:#333
    style Stage3 fill:#e1f5fe,stroke:#01579b
    style Stage4 fill:#fff4dd,stroke:#d4a017
    style Stage5 fill:#ccffcc,stroke:#2e7d32
```

## Logs & Artifacts

| File | Role |
| :--- | :--- |
| `audit_log.csv` | Master ledger including Stage 0-3 metadata and **Stage 4 AI Verification results** (Decision, Confidence, and Overlap metrics). |
| `handover_packets/*.json` | Structured payloads containing evidence-ready hunks and verification targets. |
| `llm_verification_results/*.json` | Detailed per-candidate logs of the Pass 1/Pass 2 verification workflow. |
| `diff_archive/*.diff` | Raw change evidence. |

---

## Evaluation & Metrics

The system includes an evaluation suite (`evaluate_tripwire_llm.py`) to track pipeline performance:
* **Retrieval Precision/Recall**: Measures Stage 3's ability to find the correct candidate pages.
* **Verifier Precision/Recall**: Measures Stage 4's accuracy in confirming impacts.
* **Chunk Metrics**: Tracks the accuracy of Pass 2 in identifying specific sections for review.

