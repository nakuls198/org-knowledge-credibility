"""Run credibility_engine_v2 (honest-eval working tree) on the shared claim set. Read-only use of that repo.

Usage (from that repo's venv):
  <capstone>/credibility_engine_v2/.venv/Scripts/python.exe run_v2.py <claim_set.json> <out.json>
"""

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3] / "credibility_engine_v2"
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from services import engine  # noqa: E402

claims = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
chunks = engine.load_chunks()
retriever = engine.build_retriever(chunks)
out = {}
for c in claims:
    t0 = time.perf_counter()
    try:
        r = engine.run_claim(c["text"], c["id"], retriever, chunks, use_llm=False)
        out[c["id"]] = {"raw": r["scored"]["status"], "score": r["scored"].get("credibility_score"),
                        "stance_method": r.get("stance_method"), "must_fetch": r.get("must_fetch"),
                        "seconds": round(time.perf_counter() - t0, 2)}
    except Exception as exc:  # noqa: BLE001
        out[c["id"]] = {"raw": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
    print(c["id"], out[c["id"]].get("raw"), flush=True)
Path(sys.argv[2]).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
