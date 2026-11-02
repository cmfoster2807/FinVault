"""Convert FinVault's normal (benign) datasets into a synthesis attack type named "benign".

The runner can then execute them with  --synthesis --attack-type benign  through the same loop and
defenses as the attacks. On these runs any defense block is a false positive.

Run from the repo root:  python -m veriguard.make_benign
Writes sandbox/attack_datasets_synthesis/benign/scenario_XX_attacks.json (originals untouched).
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "sandbox" / "normal_datasets"
DST = REPO / "sandbox" / "attack_datasets_synthesis" / "benign"


def convert(path: Path) -> tuple[str, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    sid = str(data.get("scenario_id") or path.stem.split("_")[1])
    cases = []
    for q in data.get("queries", []):
        case = dict(q)  # keep every original field (context, ids, expected outcome, ...)
        case["id"] = q.get("id")
        case["vulnerability"] = q.get("related_vulnerability", "benign")
        case["attack_type"] = "benign"
        case["technique"] = "none"
        case["attack_goal"] = "none"
        case["attack_prompt"] = q.get("query_prompt", "")
        case.setdefault("context", {})
        case.setdefault("multi_turn", False)
        case["is_attack"] = False
        cases.append(case)
    out = {
        "scenario_id": sid,
        "scenario_name": data.get("scenario_name", ""),
        "description": f"Benign cases converted from {path.name} for false-positive testing",
        "vulnerabilities": data.get("vulnerability_coverage", []),
        "synthesis_info": {"source": f"normal_datasets/{path.name}", "converted_by": "veriguard/make_benign.py"},
        "attacks": cases,
    }
    DST.mkdir(parents=True, exist_ok=True)
    (DST / f"scenario_{sid}_attacks.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return sid, len(cases)


def main():
    files = sorted(SRC.glob("scenario_*_normal.json"))
    if not files:
        raise SystemExit(f"No normal datasets in {SRC}")
    total = 0
    for f in files:
        sid, n = convert(f)
        total += n
        print(f"[{sid}] {n} benign cases")
    print(f"Wrote {len(files)} files, {total} cases to {DST}")


if __name__ == "__main__":
    main()
