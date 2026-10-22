"""Score every system's predictions on the shared claim set with one rule set.

Reports per claim set (client / test / team):
  4-class accuracy  : SUPPORTED / CONTRADICTED / CONTESTED / INSUFFICIENT
  binary accuracy   : credible (SUPPORTED) vs not credible (everything else) - robust to vocabulary differences
plus a per-claim table. Writes comparison.json and comparison.md.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_claim_set import COARSE  # noqa: E402

SYSTEMS = {
    "decidr_data_credibility (keyword judge)": "preds_decidr_heuristic.json",
    "credibility_engine_v2 (NLI)": "preds_v2.json",
    "org-knowledge-credibility (rules+NLI)": "preds_okc.json",
}


def main() -> None:
    claims = json.loads((HERE / "claim_set.json").read_text(encoding="utf-8"))
    preds = {name: json.loads((HERE / f).read_text(encoding="utf-8")) for name, f in SYSTEMS.items() if (HERE / f).exists()}
    sets = ["client", "test", "team", "all"]
    summary: dict = {}
    for name, p in preds.items():
        summary[name] = {}
        for s in sets:
            rows = [c for c in claims if s == "all" or c["set"] == s]
            got = [COARSE.get(p.get(c["id"], {}).get("raw"), "ERROR") for c in rows]
            exp = [c["expected"] for c in rows]
            n = len(rows)
            acc4 = sum(g == e for g, e in zip(got, exp)) / n
            bin_ = sum((g == "SUPPORTED") == (e == "SUPPORTED") for g, e in zip(got, exp)) / n
            summary[name][s] = {"n": n, "acc4": round(acc4, 3), "binary": round(bin_, 3),
                                "correct4": sum(g == e for g, e in zip(got, exp))}
    lines = ["| System | Client (4) | Held-out test (20) | Team gold (30) | All (54) |", "|---|---|---|---|---|"]
    for name, d in summary.items():
        cells = [f"{d[s]['correct4']}/{d[s]['n']} ({d[s]['acc4']:.0%}) · bin {d[s]['binary']:.0%}" for s in sets]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "| Claim | Set | Expected | " + " | ".join(SYSTEMS) + " |", "|---|---|---|" + "---|" * len(SYSTEMS)]
    for c in claims:
        cells = []
        for name in SYSTEMS:
            raw = preds.get(name, {}).get(c["id"], {}).get("raw", "-")
            mark = "✓" if COARSE.get(raw) == c["expected"] else "✗"
            cells.append(f"{mark} {raw}")
        lines.append(f"| {c['id']} | {c['set']} | {c['expected_raw']} | " + " | ".join(cells) + " |")
    (HERE / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (HERE / "comparison.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n".join(lines[:2 + len(summary)]))


if __name__ == "__main__":
    main()
