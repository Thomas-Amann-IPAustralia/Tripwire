## Summary

This PR implements Stages 4, 5, and 6 of the document relevance and semantic matching pipeline, along with comprehensive test coverage. These stages determine whether incoming changes are relevant to the IPFR corpus and identify candidate pages for further processing.

## Key Changes

### New Source Modules

- **`src/stage4_relevance.py`** — Relevance scoring via weighted RRF fusion
  - Extracts keyphrases from diffs using YAKE
  - Scores pages using BM25 (keyword-based) and bi-encoder (semantic) signals
  - Fuses scores via Reciprocal Rank Fusion with configurable weights
  - Applies source importance multiplier and fast-pass override logic
  - Selects top-N candidates or those above a configurable threshold

- **`src/stage5_biencoder.py`** — Coarse-grained semantic matching
  - Chunks incoming change documents using fixed-size overlap strategy
  - Encodes chunks with BAAI/bge-base-en-v1.5 bi-encoder
  - Computes cosine similarity against precomputed corpus chunk embeddings
  - Identifies candidates via two decision rules: single high-scoring chunk or multiple medium-scoring chunks
  - Lazy model loading with process-lifetime caching

- **`src/stage6_crossencoder.py`** — Precise semantic reranking and graph propagation
  - Scores candidate pages using gte-reranker-modernbert-base cross-encoder
  - Blends cross-encoder scores with lexical relevance from Stage 4
  - Implements graph-based signal propagation (up to 3 hops with decay)
  - Applies additive-only propagation to avoid demoting pages
  - Includes token budget checks and truncation warnings
  - Lazy model loading with process-lifetime caching

### Test Coverage

- **`tests/test_relevance_scoring.py`** — 673 lines covering Stage 4
  - Tests for tokenization, ranking, score distribution, RRF fusion, and candidate selection
  - Mocked YAKE and bi-encoder dependencies
  - In-memory SQLite databases for all tests

- **`tests/test_semantic_matching.py`** — 806 lines covering Stages 5 and 6
  - Tests for text chunking, encoding, corpus loading, and page scoring
  - Cross-encoder scoring, reranking, and graph propagation logic
  - Observation mode data collection
  - All tests use mocked ML models and in-memory databases

### Dependencies

- Added `rank-bm25>=0.2` for BM25 keyword scoring
- Existing `sentence-transformers>=2.7` used for both bi-encoder and cross-encoder

## Notable Implementation Details

- **Lazy model loading**: Both bi-encoder and cross-encoder use process-lifetime caches to avoid redundant model loads
- **Observation mode**: All stages capture score distributions and metadata for pipeline logging when enabled
- **Graph propagation**: Implements additive-only signal propagation with configurable decay and hop limits
- **Token budget awareness**: Stage 6 logs warnings when combined input exceeds the cross-encoder's context window
- **Configurable thresholds**: All decision rules (high/medium chunk scores, RRF thresholds, cross-encoder threshold) are configurable
- **Fast-pass override**: Stage 4 can bypass candidate selection entirely when source importance meets a threshold

https://claude.ai/code/session_01RcuuGHTa6AXihBUHFoFaAC
