# Tripwire Prototype – Markdown & JSON-LD Updates

## Purpose

These changes prepare IP First Response content files so they can be reliably consumed by the **Tripwire LLM verification stage**.
The goal is to allow the LLM to:

1. Open the correct IPFR page.
2. Navigate to the correct section deterministically.
3. Compare external changes (diff hunks) with the relevant IPFR content.
4. Produce a clear, auditable decision about whether the change impacts IPFR guidance.

The updates are intentionally minimal and designed only to support **prototype testing**.

---

# Overview of Changes

Two types of files were updated:

* **Markdown content pages**
* **JSON-LD structured data files**

The changes create a stable link between:

```
Tripwire handover packet
      ↓
semantic chunk candidates
      ↓
IPFR page (UDID)
      ↓
specific page section
```

---

# Markdown Changes

## 1. Added YAML front matter

Each markdown page now begins with metadata describing the page.

Example:

```markdown
---
udid: 101-1
ipfr_url: https://ipfirstresponse.ipaustralia.gov.au/options/how-avoid-infringing-others-intellectual-property
title: How to avoid infringing others' intellectual property
---
```

### Purpose

* Allows the LLM to confirm it opened the correct page.
* Provides a stable identifier (`udid`) that matches the Tripwire semantic embeddings and handover packet.

---

## 2. Added stable section anchors

Major headings now have a section identifier placed immediately above them.

Example:

```markdown
<!-- section_id: section-1-avoiding-ip-infringement -->
### Avoiding IP infringement
```

### Purpose

This gives the LLM a deterministic navigation target.

Instead of fuzzy matching headings, the LLM can reference:

```
UDID: 101-1
Section: section-1-avoiding-ip-infringement
```

These identifiers correspond directly to the JSON-LD section identifiers.

---

## 3. No chunk IDs added to markdown

Chunk IDs remain **external to the content files** and are stored only in the semantic embeddings file.

### Reason

Chunk IDs are artifacts of the embedding pipeline and can change whenever:

* content is edited
* chunking logic changes
* embeddings are regenerated

Keeping them out of markdown prevents unnecessary content churn.

---

# JSON-LD Changes

## 1. Added `identifier` to each section

Each `WebPageElement` section now includes a short identifier matching the markdown anchor.

Example:

```json
{
  "@type": "WebPageElement",
  "@id": "https://ipfirstresponse.ipaustralia.gov.au/options/how-avoid-infringing-others-intellectual-property#section-1-avoiding-ip-infringement",
  "identifier": "section-1-avoiding-ip-infringement",
  "headline": "Avoiding IP infringement",
  "text": "..."
}
```

### Purpose

Allows the LLM to reference sections using a short ID instead of long URLs.

Example citation:

```
101-1 → section-1-avoiding-ip-infringement
```

---

## 2. Added `isPartOf` links

Sections now explicitly reference their parent page:

```json
"isPartOf": { "@id": "...#webpage" }
```

### Purpose

Clarifies the relationship between sections and the main WebPage entity.

This improves structured navigation when the LLM reads JSON-LD.

---

# How the LLM Uses These Changes

During Tripwire verification the LLM follows this process:

1. **Receive candidate page from the handover packet**

```
UDID: 101-1
best_chunk_id: 101-1-C07
```

2. **Resolve the chunk in the embeddings file**

This returns a text snippet and confirms the page UDID.

3. **Open the markdown page**

```
101-1 - How to avoid infringing others' intellectual property.md
```

4. **Navigate to the relevant section**

Using the section identifier:

```
section-1-avoiding-ip-infringement
```

5. **Compare the section text with the external change**

6. **Return a decision**

```
impact
no impact
uncertain
```

---

# Files Updated for Prototype

The following files were updated for testing:

### Markdown

* `101-1 - How to avoid infringing others' intellectual property.md`
* `101-2 - Design infringement.md`

### JSON-LD

* `101-1_how-to-avoid-infringing-others-intellectual-property.json`
* `101-2_design-infringement.json`

---

# Prototype Scope

These changes are intentionally minimal and designed only to support the Tripwire prototype.

Future improvements may include:

* section summaries for faster LLM triage
* consistent FAQ question identifiers
* automated section anchor generation during markdown export

---

# Summary

The prototype updates introduce **three key capabilities**:

1. **Page identification** via `udid`
2. **Deterministic section navigation** via `section_id`
3. **Structured section referencing** via JSON-LD `identifier`

Together these allow the Tripwire LLM verification stage to reliably locate, inspect, and cite IP First Response content when evaluating external changes.
