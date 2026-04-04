import os
import logging

# --- Configuration ---
AUDIT_LOG = 'audit_log.csv'
SOURCES_FILE = 'sources.json'
OUTPUT_DIR = 'content_archive'
DIFF_DIR = 'diff_archive'
HANDOVER_DIR = 'handover_packets'
SEMANTIC_EMBEDDINGS_FILE = 'Semantic_Embeddings_Output.json'
CURRENT_RUN_MANIFEST = 'current_run_manifest.json'

# --- IPFR content archive for LLM verification (prototype) ---
# NOTE: Prototype only. We resolve UDIDs to content files by filename patterns within IPFR_CONTENT_ARCHIVE_DIR.
# In production, prefer an explicit UDID->file map generated during the IPFR export pipeline.
IPFR_CONTENT_ARCHIVE_DIR = os.environ.get("IPFR_CONTENT_ARCHIVE_DIR", "IPFR_content_archive")
LLM_VERIFY_DIR = os.environ.get("LLM_VERIFY_DIR", "llm_verification_results")
UPDATE_SUGGESTIONS_DIR = os.environ.get("UPDATE_SUGGESTIONS_DIR", "llm_update_suggestions")

# Prototype behaviour: only load the top N candidates to keep prompts small.
# NOTE: This will need to be changed later to load the specific sections needed, not whole pages,
# and to support explicit per-candidate section targets.
TOP_N_VERIFICATION_CANDIDATES = int(os.environ.get("TOP_N_VERIFICATION_CANDIDATES", "3"))

# LLM verification execution
# Prototype requirement: always run verification after handover packets are generated.
# (If OPENAI_API_KEY is missing, verification will fail closed to overall_decision="uncertain".)
LLM_MODEL = os.environ.get("TRIPWIRE_LLM_MODEL", "gpt-4.1-mini")
STAGE5_LLM_MODEL = os.environ.get("TRIPWIRE_STAGE5_LLM_MODEL", LLM_MODEL)
TAGS_TO_EXCLUDE = ['nav', 'footer', 'header', 'script', 'style', 'aside', '.noprint', '#sidebar', 'iframe']

# Semantic scoring config
SEMANTIC_MODEL = 'text-embedding-3-small'

# Candidate / packet policy
CANDIDATE_MIN_SCORE = 0.35  # all page candidates >= this are "relevant" and must be handed over (across batches) if handover triggers
MEDIUM_PRIMARY_HANDOVER_THRESHOLD = 0.45
LOW_PRIMARY_HANDOVER_THRESHOLD = 0.50

# No top-k chunk cap: keep every chunk match >= threshold for evidence aggregation
HUNK_CHUNK_MIN_SIMILARITY = CANDIDATE_MIN_SCORE

# Packet/display controls (do not truncate threshold-passing candidates overall; batching handles overflow)
MAX_CANDIDATES_PER_PACKET = 50
MAX_RELEVANT_CHUNK_IDS_PER_CANDIDATE = 50
PER_HUNK_SUMMARY_LIMIT = 8

# Page aggregation bonuses
PAGE_HUNK_COVERAGE_BONUS = 0.04
MAX_PAGE_COVERAGE_BONUS = 0.12
PAGE_CHUNK_DENSITY_BONUS = 0.01
MAX_PAGE_DENSITY_BONUS = 0.06

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Tripwire")


# --- Audit log schema (human-friendly, prototype) ---

AUDIT_HEADERS = [
    'Timestamp', 'Source_Name', 'Priority', 'Status', 'Change_Detected',
    'Version_ID', 'Diff_File', 'Similarity_Score', 'Power_Words',
    'Matched_UDID', 'Matched_Chunk_ID', 'Outcome', 'Reason',

    # Human-friendly AI verification linkage
    'AI Verification Run',
    'AI Verification Time',
    'AI Model Used',
    'AI Decision',
    'AI Confidence',
    'AI Change Summary',
    'AI Verification File',
    'Human Review Needed',

    # Monitoring / performance
    'Similarity Predicted Pages',
    'AI Verified Impact Pages',
    'AI vs Similarity Overlap Score',
    'AI vs Similarity Precision',
    'AI vs Similarity Recall',
    'Overlap Details',

    # Stage 5 AI update suggestion linkage
    'AI Update Suggestion Run',
    'AI Update Suggestion Time',
    'AI Update Suggestion Status',
    'AI Update Suggested Chunks',
    'AI Update Suggestions File',
    'AI Update Review Notes'
]
