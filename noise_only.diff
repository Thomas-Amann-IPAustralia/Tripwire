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

## 3. Chunk markers added to markdown

Markdown pages now include **hidden chunk markers** so Tripwire and the LLM can deterministically locate the exact text block that corresponds to the `best_chunk_id` / `relevant_chunk_ids`.

Example:

<!-- chunk_id: 101-2-C04 -->

### Why this is required

Without chunk markers, the LLM must "search" the page for relevant text, which increases uncertainty and makes it hard to generate precise rewrite suggestions.

### Compatibility and drift

Chunk markers are **invisible** in the rendered site (HTML comments) and are intended to be stable identifiers for Tripwire’s prototype.
The semantic embeddings file remains the retrieval index, but the **markdown is the source of truth** for applying updates.

---
