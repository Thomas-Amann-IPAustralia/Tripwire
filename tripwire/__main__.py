import json
import os
import sys

from .config import logger
from .stage3_score import calculate_similarity
from .pipeline import main

if len(sys.argv) > 1 and sys.argv[1] == '--test-stage3':
    if len(sys.argv) < 3:
        logger.error("Usage: python -m tripwire --test-stage3 <path_to_diff_file>")
        sys.exit(1)
    diff_file = sys.argv[2]
    if not os.path.exists(diff_file):
        logger.error(f"Diff file not found: {diff_file}")
        sys.exit(1)

    result = calculate_similarity(diff_file, source_priority='High')
    print(json.dumps({
        'status': result.get('status'),
        'primary_udid': result.get('primary_udid'),
        'primary_score': result.get('page_final_score'),
        'candidate_count': result.get('candidate_count'),
        'multi_impact_likely': result.get('multi_impact_likely'),
        'should_handover': result.get('should_handover'),
        'handover_decision_reason': result.get('handover_decision_reason')
    }, indent=2))
    sys.exit(0)

main()
