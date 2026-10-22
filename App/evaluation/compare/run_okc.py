"""Run org-knowledge-credibility on the shared claim set.

Usage (from backend/ with its venv; DATABASE_URL etc. set by the caller):
  .venv/Scripts/python.exe ../evaluation/compare/run_okc.py <claim_set.json> <out.json> [classifier_mode]
"""

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
logging.disable(logging.WARNING)

from app.assessments.service import investigate  # noqa: E402
from app.database.session import get_sessionmaker  # noqa: E402

mode = sys.argv[3] if len(sys.argv) > 3 else "rules+nli"
claims = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out = {}
with get_sessionmaker()() as db:
    for c in claims:
        t0 = time.perf_counter()
        try:
            o = investigate(db, c["text"], classifier_mode=mode, narrative=False)
            out[c["id"]] = {"raw": o.result.verdict, "score": o.result.score, "needs_review": o.result.needs_review,
                            "seconds": round(time.perf_counter() - t0, 2)}
        except Exception as exc:  # noqa: BLE001
            out[c["id"]] = {"raw": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        print(c["id"], out[c["id"]].get("raw"), flush=True)
        Path(sys.argv[2]).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")  # save as we go
