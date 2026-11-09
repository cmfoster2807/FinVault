"""Significance tests for the suite: is VeriGuard different from GuardRail (and from no defense)?

Run from the repo root:  python -m veriguard.sig_test
Uses only finished, valid runs in results/suite_uf_gpt_oss_120b/. No API calls.

Benign false blocks are paired per case (the same case ran under each arm), so the main test is an
exact McNemar test on the discordant cases, plus a paired bootstrap 95% CI on the difference.
Attack success is paired too when the runner's per-case results are available; otherwise it falls
back to unpaired Wilson intervals.
"""
import argparse
import json
import math
import random
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
R = REPO / "results" / "suite_uf_gpt_oss_120b"
BENIGN = REPO / "sandbox" / "attack_datasets_synthesis" / "benign"
ARMS = ["none", "guardrail", "veriguard", "veriguard_hist"]
PAIRS = [("guardrail", "veriguard"), ("guardrail", "veriguard_hist"), ("veriguard", "veriguard_hist"),
         ("none", "guardrail"), ("none", "veriguard")]


def binom_two_sided(k, n):
    """Exact two-sided binomial test, p = 0.5 (McNemar exact)."""
    if n == 0:
        return 1.0
    k = min(k, n - k)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def bootstrap_diff(a, b, reps=10000, seed=0):
    """Paired bootstrap CI for mean(b) - mean(a), resampling cases."""
    rng = random.Random(seed)
    n = len(a)
    diffs = []
    for _ in range(reps):
        idx = [rng.randrange(n) for _ in range(n)]
        diffs.append(sum(b[i] - a[i] for i in idx) / n)
    diffs.sort()
    return diffs[int(0.025 * reps)], diffs[int(0.975 * reps)]


def load_summary(sid, arm, ds):
    f = R / sid / f"{arm}__{ds}.summary.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def benign_case_ids(sid):
    f = BENIGN / f"scenario_{sid}_attacks.json"
    if not f.exists():
        return []
    return [c["id"] for c in json.loads(f.read_text(encoding="utf-8")).get("attacks", [])]


def per_case_success(sid, arm, ds):
    """{case_id: bool} from the runner's own report, if it lists per-case results."""
    f = R / sid / f"{arm}__{ds}.json"
    if not f.exists() or load_summary(sid, arm, ds) is None:
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    found = {}

    def walk(x):
        if isinstance(x, dict):
            if "case_id" in x and "attack_success" in x:
                found[x["case_id"]] = bool(x["attack_success"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return found or None


def paired_report(title, vectors, note=""):
    print(f"\n== {title}")
    if note:
        print(f"   {note}")
    n = len(next(iter(vectors.values()))) if vectors else 0
    print(f"   paired cases: {n}")
    for arm in ARMS:
        if arm in vectors:
            k = sum(vectors[arm])
            lo, hi = wilson(k, n)
            print(f"   {arm:15s} {k:3d}/{n}  {100 * k / n:5.1f}%   95% CI {100 * lo:5.1f}-{100 * hi:5.1f}%")
    print(f"   {'comparison':32s} {'only A':>6s} {'only B':>6s}  {'diff B-A':>8s}  {'95% CI':>15s}  {'p (McNemar)':>11s}")
    for a, b in PAIRS:
        if a not in vectors or b not in vectors:
            continue
        va, vb = vectors[a], vectors[b]
        only_a = sum(1 for x, y in zip(va, vb) if x and not y)
        only_b = sum(1 for x, y in zip(va, vb) if y and not x)
        diff = (sum(vb) - sum(va)) / n
        lo, hi = bootstrap_diff(va, vb)
        p = binom_two_sided(only_a, only_a + only_b)
        print(f"   A={a:13s} B={b:14s} {only_a:6d} {only_b:6d}  {100 * diff:+7.1f}pt  "
              f"[{100 * lo:+5.1f}, {100 * hi:+5.1f}]  {p:11.3f}")


def main():
    ap = argparse.ArgumentParser(description="Paired significance tests on suite results")
    ap.add_argument("--exclude-risky-benign", action="store_true",
                    help="Drop benign cases where the undefended agent itself triggered a vulnerability")
    args = ap.parse_args()
    scenarios = sorted(p.name for p in R.iterdir() if p.is_dir() and p.name.isdigit())

    # ---- benign false blocks, paired per case
    vec = {arm: [] for arm in ARMS}
    used, skipped = 0, []
    for sid in scenarios:
        sums = {arm: load_summary(sid, arm, "benign") for arm in ARMS}
        if any(s is None for s in sums.values()):
            skipped.append(sid)
            continue
        cases = benign_case_ids(sid)
        if not cases:
            skipped.append(sid)
            continue
        risky = set()
        if args.exclude_risky_benign:
            pcs = per_case_success(sid, "none", "benign")
            if pcs:
                risky = {c for c, s in pcs.items() if s}
            elif sums["none"].get("succ"):
                skipped.append(sid)  # can't tell which cases, drop the scenario
                continue
        used += 1
        for c in cases:
            if c in risky:
                continue
            for arm in ARMS:
                vec[arm].append(1 if c in set(sums[arm].get("blocked_cases", [])) else 0)
    paired_report("Benign cases with at least one block (lower is better)", vec,
                  f"{used} scenarios with all 4 arms valid; skipped: {', '.join(skipped) or 'none'}")

    # ---- attack success on the original set
    per_case = {}
    for sid in scenarios:
        row = {arm: per_case_success(sid, arm, "original") for arm in ARMS}
        if all(row.values()):
            common = set.intersection(*(set(v) for v in row.values()))
            for c in sorted(common):
                per_case[(sid, c)] = {arm: int(row[arm][c]) for arm in ARMS}
    if per_case:
        vec = {arm: [per_case[k][arm] for k in sorted(per_case)] for arm in ARMS}
        paired_report("Attack success on the original set (lower is better)", vec,
                      "paired per case from the runner's per-case results")
    else:
        print("\n== Attack success on the original set (unpaired: no per-case results found)")
        for arm in ARMS:
            k = n = 0
            for sid in scenarios:
                s = load_summary(sid, arm, "original")
                if s:
                    k += s.get("succ") or 0
                    n += s.get("total") or 0
            if n:
                lo, hi = wilson(k, n)
                print(f"   {arm:15s} {k:3d}/{n}  {100 * k / n:5.1f}%   95% CI {100 * lo:5.1f}-{100 * hi:5.1f}%")
        print("   Overlapping intervals between defended arms = no detectable difference.")


if __name__ == "__main__":
    main()
