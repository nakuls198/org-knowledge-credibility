"""Run decidr_data_credibility (current main) on the shared claim set. Read-only use of that repo.

Runs with persist=False (nothing written to its run history) and the offline
"heuristic" stance engine by default. Its LLM judge sends passages to Gemini/Groq,
so it is only used when explicitly requested:  run_decidr.py <in> <out> llm

Usage (from that repo's venv):
  <capstone>/decidr_data_credibility/.venv/Scripts/python.exe run_decidr.py <claim_set.json> <out.json> [heuristic|llm]
"""

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3] / "decidr_data_credibility"
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from backend.services import checker  # noqa: E402

engine_name = sys.argv[3] if len(sys.argv) > 3 else "heuristic"
claims = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out = {}
for c in claims:
    t0 = time.perf_counter()
    try:
        r = checker.run_claim(c["text"], claim_id=c["id"], stance_engine=engine_name, explain_with_llm=False,
                              persist=False)
        scored = r.get("scored") or {}
        out[c["id"]] = {"raw": scored.get("status") or r.get("status_label") or r.get("status"),
                        "score": scored.get("credibility_score"), "notices": r.get("notices"),
                        "seconds": round(time.perf_counter() - t0, 2)}
    except Exception as exc:  # noqa: BLE001
        out[c["id"]] = {"raw": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
    print(c["id"], out[c["id"]].get("raw"), flush=True)
Path(sys.argv[2]).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
