"""Build the shared claim set for the cross-version comparison.

Sets (labels are never shown to the systems, only claim text):
  client  - the 4 claims in the client's labelled_claims_sample.csv (only labels nobody on the team wrote)
  test    - the 21 held-out test claims of evaluation/datasets/claims.csv (author-drafted, never used for tuning)
  team    - the 30 team gold claims from credibility_engine_v2/data/gold/gold_claims.json

Every expected label is mapped to a common 4-class scheme:
  SUPPORTED     <- Supported, Likely, Formal truth, PARTIALLY_SUPPORTED
  CONTRADICTED  <- Unsupported, Unlikely, CONTRADICTED
  CONTESTED     <- Conflicted, CONTESTED
  INSUFFICIENT  <- INSUFFICIENT_EVIDENCE, Not enough evidence
"""

import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CAPSTONE = ROOT.parent

COARSE = {
    "Supported": "SUPPORTED", "Likely": "SUPPORTED", "SUPPORTED": "SUPPORTED", "PARTIALLY_SUPPORTED": "SUPPORTED",
    "Formal truth": "SUPPORTED",
    "Unsupported": "CONTRADICTED", "Unlikely": "CONTRADICTED", "CONTRADICTED": "CONTRADICTED",
    "Conflicted": "CONTESTED", "CONTESTED": "CONTESTED",
    "INSUFFICIENT_EVIDENCE": "INSUFFICIENT", "Not enough evidence": "INSUFFICIENT",
}


def main() -> None:
    claims = []
    with (ROOT / "evaluation" / "datasets" / "claims.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["label_source"] == "client_sample":
                claims.append({"id": r["claim_id"], "set": "client", "text": r["reference_claim"],
                               "expected_raw": r["reference_status"], "expected": COARSE[r["reference_status"]]})
            elif r["split"] == "test":
                claims.append({"id": r["claim_id"], "set": "test", "text": r["reference_claim"],
                               "expected_raw": r["expected_verdict"], "expected": COARSE[r["expected_verdict"]]})
    gold = json.loads((CAPSTONE / "credibility_engine_v2" / "data" / "gold" / "gold_claims.json").read_text(encoding="utf-8"))
    for g in gold["claims"] if isinstance(gold, dict) else gold:
        claims.append({"id": g["claim_id"], "set": "team", "text": g["claim_text"],
                       "expected_raw": g["expected_status"], "expected": COARSE[g["expected_status"]]})
    (HERE / "claim_set.json").write_text(json.dumps(claims, indent=2), encoding="utf-8")
    from collections import Counter
    print(len(claims), Counter((c["set"], c["expected"]) for c in claims))


if __name__ == "__main__":
    main()
