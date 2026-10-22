"""Developer tool: print the full evidence trace for one claim (dev-split debugging only).

  python scripts/debug_claim.py E13            # by evaluation claim id
  python scripts/debug_claim.py "free text claim"
"""

import json
import logging
import sys

sys.path.insert(0, ".")
logging.disable(logging.WARNING)

from app.assessments.service import assess_text  # noqa: E402
from app.evaluation.runner import load_claims  # noqa: E402

arg = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else None
claims = {c["claim_id"]: c for c in load_claims()}
if arg in claims:
    c = claims[arg]
    if c["split"] == "test":
        sys.exit("refusing to debug a test-split claim (held out)")
    text = c["reference_claim"]
    print("EXPECTED", c["expected_verdict"], "| sup:", c["supporting"], "| con:", c["contradicting"])
else:
    text = arg
r = assess_text(text, classifier=mode)
a = r["claim"]
print("CLAIM", a["claim_type"], a["quantifier"], "neg" if a["negated"] else "", [x["mention"] for x in a["actors"]],
      [n["raw"] for n in a["numbers"]])
res = r["result"]
print("VERDICT", res["verdict"], res["score"], "S", res["support_mass"], "C", res["contradict_mass"], "cov", res["coverage"])
for e in r["evidence"]:
    print(f"{e['rank']:2} {e['label']:11} {e['scope']:10} w={e['weight']:.3f} {e['cluster'] or '':4} {e['section'][:40]:40} | {e['span'][:110]!r}")
    print("     ", e["rationale"][:230])
print(json.dumps({k: res[k] for k in ("temporal_notes", "caveats", "review_reasons", "missing_information")}, indent=1)[:1500])
