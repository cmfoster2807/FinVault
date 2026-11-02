"""Why are benign cases blocked? Break down every block on the benign dataset.

Run from the repo root:  python -m veriguard.diagnose_benign
Checks, per defense arm:
  - errors       blocks caused by an extractor/judge failure (fail-closed), not a real decision
  - history cap  blocks where the guard saw the maximum number of past calls, so an earlier
                 verification result may have been cut off (a cost setting, not the method)
  - all-null     VeriGuard blocks where every extracted argument came back empty
  - tools / reasons that account for most blocks
  - whether the undefended agent itself triggered a vulnerability on that benign case
    (if it did, blocking it may be correct rather than a false positive)
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

R = Path("results/suite_uf_gpt_oss_120b")
HISTORY_CAP = 4  # enforce.CONFIG["history_items"] during the suite


def main():
    # benign cases where the undefended agent triggered a vulnerability
    risky = defaultdict(int)
    for f in R.glob("*/none__benign.summary.json"):
        s = json.loads(f.read_text())
        risky[s["scenario"]] = s.get("succ") or 0
    print(f"Undefended agent triggered a vulnerability on {sum(risky.values())} benign cases "
          f"(scenarios: {', '.join(f'{k}:{v}' for k, v in sorted(risky.items()) if v) or 'none'})\n")

    for arm in ("guardrail", "veriguard", "veriguard_hist"):
        blocks = []
        for f in sorted(R.glob(f"*/{arm}__benign.guard.jsonl")):
            for line in f.read_text().splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not row.get("allowed", True):
                    blocks.append(row)
        if not blocks:
            print(f"== {arm}: no blocks\n")
            continue
        cases = {(b.get("scenario"), b.get("case_id")) for b in blocks}
        errors = [b for b in blocks if b.get("error")]
        capped = [b for b in blocks if b.get("history_seen", "").count("CALL ") >= HISTORY_CAP]
        no_hist = [b for b in blocks if b.get("history_seen", "") in ("(none)", "")]
        all_null = [b for b in blocks if arm != "guardrail" and b.get("arguments")
                    and all(v is None for k, v in b["arguments"].items() if k != "tool_name")]
        n = len(blocks)
        print(f"== {arm}: {n} blocks across {len(cases)} benign cases")
        print(f"   fail-closed errors          {len(errors):4d} ({100 * len(errors) / n:4.1f}%)")
        print(f"   history at cap ({HISTORY_CAP} calls)    {len(capped):4d} ({100 * len(capped) / n:4.1f}%)")
        print(f"   no prior tool calls at all  {len(no_hist):4d} ({100 * len(no_hist) / n:4.1f}%)")
        if arm != "guardrail":
            print(f"   all extracted args null     {len(all_null):4d} ({100 * len(all_null) / n:4.1f}%)")
        print("   most-blocked tools:  " + ", ".join(f"{t} ({c})" for t, c in Counter(b.get("tool", "?") for b in blocks).most_common(6)))
        print("   most common reasons:")
        for r, c in Counter(b.get("reason", "")[:90] for b in blocks).most_common(6):
            print(f"     {c:4d}  {r}")
        first_per_case = {}
        for b in blocks:
            first_per_case.setdefault((b.get("scenario"), b.get("case_id")), b)
        repeats = n - len(first_per_case)
        print(f"   repeat blocks (agent retried after a block): {repeats} of {n}\n")


if __name__ == "__main__":
    main()
