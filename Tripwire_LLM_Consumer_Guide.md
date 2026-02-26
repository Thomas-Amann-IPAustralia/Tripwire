# Tripwire LLM Consumer Guide
**Audience:** Analysts & LLM Operators (e.g. Tom)  
**Purpose:** Correct interpretation and use of Tripwire handover packets
## Core Principle
Tripwire does **not determine impact**. Tripwire identifies plausible semantic impacts, candidate pages requiring inspection, and evidence supporting those hypotheses. The LLM confirms impact, interprets meaning changes, and suggests updates.
## What Tripwire Has Already Done
**Noise Suppression:** Administrative changes (page numbers, standalone dates, formatting fragments) are excluded. Absence of candidates ≠ failure.  
**Semantic Hunk Parsing:** Changes are isolated into meaning-bearing regions. Reason over hunks, not raw diffs.  
**Similarity Signals:** Hunk embeddings are compared to semantic chunks. Scores represent likelihood, not truth.  
**Score Stabilizers:** Coverage, density, and power word uplift modify ranking only.
## Your Role
Primary question: **Does this source change materially alter the meaning of any candidate pages?** Not: “Which page has the highest score?” or “Is Tripwire correct?”
## Understanding Candidates
Candidate pages are hypotheses. High score = strong semantic proximity, not guaranteed update. Multiple candidates are normal due to shared terminology and cross-page dependencies.
## Evaluating a Candidate Page
**Step 1 – Hunk Context:** What changed? Meaning or surface change? Ignore diff syntax.  
**Step 2 – Page Intent:** Does the change alter obligations, rights, penalties, deadlines, risks, or interpretation?  
**Step 3 – Chunk Evidence:** Chunk IDs explain association; they are retrieval hints, not edit instructions.  
**Step 4 – Impact Class:** No impact, clarification, substantive update, terminology alignment, or risk change.
## Interpretation Rules
**Scores = Signals:** Never treat similarity as a verdict.  
**Thresholds = Cost Controls:** Thresholds regulate LLM usage and noise suppression.  
**Power Words:** Increase scrutiny only.  
**Noise Flags:** Strong indicator of low semantic risk.  
**Diff Preview:** Informational only.
## Recommended Prompting Pattern
“Given this Tripwire packet, determine whether the source change materially affects any candidate pages. Explain your reasoning.”
## Common Errors
- Mistaking similarity for correctness  
- Over-focusing on highest score  
- Ignoring multi-impact cases  
- Treating chunk IDs as instructions
## Escalation Guidance
Escalate when legal obligations, penalties, compliance risks, terminology shifts, or uncertainty are present.
## Trust Model
Tripwire = deterministic evidence generator  
LLM = probabilistic reasoning layer  
Neither alone determines truth.
## Mental Model
Tripwire asks: **What might be impacted?**  
LLM + Analyst decide: **What is actually impacted?**
