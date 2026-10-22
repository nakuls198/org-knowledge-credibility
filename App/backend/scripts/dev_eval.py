"""Developer loop: evaluate the DEV split only and print a compact per-claim table.

  python scripts/dev_eval.py [classifier_mode]
The test split is never printed here; use `python -m app.cli evaluate` for the full report.
"""

import json
import logging
import sys

sys.path.insert(0, ".")
logging.disable(logging.WARNING)

from app.evaluation.runner import run_evaluation  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "rules"
r = run_evaluation(split="dev", classifiers=[mode], save=False, retrieval_modes=["bm25"])
b = r["classifiers"][mode]
for c in b["claims"]:
    mark = "OK" if c["expected"] == c["predicted"] else "--"
    f = c["features"]
    print(f"{mark} {c['claim_id']} {c['claim_type'][:4]} {c['expected']:22} {c['predicted']:22} {c['score']:.2f} "
          f"S={f['support_mass']:.2f} C={f['contradict_mass']:.2f}")
v, st = b["verdict"]["dev"], b["stance"]["dev"]
print(json.dumps({"verdict_acc": v["accuracy"], "macro_f1": v["macro_f1"], "stance_lenient": st["accuracy_lenient"],
                  "stance_per_class": st["per_class"], "evidence_recall": st["evidence_recall"],
                  "brier": b["calibration"]["dev"].get("brier"), "seconds": b["seconds_total"]}, indent=1))
print("stance confusion (rows S,C,MIXED; cols S,C,MIXED,NONE):", st["confusion"]["matrix"])
