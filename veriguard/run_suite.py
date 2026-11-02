"""Run the full comparison suite: scenarios x defenses x datasets, under a dollar budget.

Arms (rows of the paper's Table 1, plus one ablation):
  none            no defense
  guardrail       the paper's GuardRail baseline (LLM judges each call directly)
  veriguard       the paper's method (generated policy + LLM argument extraction, full context)
  veriguard_hist  ablation: VeriGuard with the extractor limited to executed tool history

Datasets:
  original   sandbox/attack_datasets (default runner dataset)
  benign     normal_datasets converted by make_benign.py (any block here is a false positive)
  synthesis  the 8 synthesis attack types (per-attack-type ASR)

Run from the repo root:
  python -m veriguard.run_suite --prepare --budget 1          # specs, benign files, policies only
  python -m veriguard.run_suite --budget 20 --jobs 3           # run everything still missing
  python -m veriguard.run_suite --report                       # tables from finished runs
Finished runs are skipped, so the same command resumes after a stop or a crash.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
RUNNER = REPO / "sandbox" / "run_attack_test.py"

ARMS = {
    "none":           {"defense": None,        "context": "full"},
    "guardrail":      {"defense": "guardrail", "context": "full"},
    "veriguard":      {"defense": "veriguard", "context": "full"},
    "veriguard_hist": {"defense": "veriguard", "context": "history"},
}
SYNTH_TYPES = ["authority_impersonation", "direct_json_injection", "emotional_manipulation",
               "encoding_disguise", "gradual_induction", "hypothetical_scenario",
               "instruction_override", "roleplay_induction"]
DATASET_ORDER = ["original", "benign", "synthesis"]

# NaviGator prices, USD per 1M tokens (input, output). Check docs.ai.it.ufl.edu before relying on them.
PRICES = {"gpt-oss-120b": (0.06, 0.15), "llama-3.3-70b-instruct": (0.40, 0.40)}
AGENT_MODEL = {"uf_gpt_oss_120b": "gpt-oss-120b", "uf_llama_3.3_70b": "llama-3.3-70b-instruct"}

_spent_lock = threading.Lock()


def cost(model, prompt, completion):
    p_in, p_out = PRICES.get(model, (0.40, 0.40))  # unknown model: price conservatively
    return (prompt * p_in + completion * p_out) / 1e6


def read_usage(path: Path):
    prompt = completion = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                prompt += row.get("prompt", 0)
                completion += row.get("completion", 0)
            except json.JSONDecodeError:
                pass
    return prompt, completion


def all_scenarios():
    return sorted(p.name.split("_")[1] for p in (REPO / "sandbox").glob("sandbox_[0-9][0-9]") if p.is_dir())


def base_env(args):
    env = dict(os.environ)
    env.update({
        "VERIGUARD_SPEC_DIR": str(args.spec_dir),
        "VERIGUARD_POLICY_DIR": str(args.policy_dir),
        "VERIGUARD_MODEL": args.vg_model,
        "VERIGUARD_MAX_TOKENS": env.get("VERIGUARD_MAX_TOKENS", "8000"),
        "VERIGUARD_QUIET": "1",
        "PYTHONUNBUFFERED": "1",
    })
    if not env.get("NAVIGATOR_API_KEY") and env.get("VERIGUARD_API_KEY"):
        env["NAVIGATOR_API_KEY"] = env["VERIGUARD_API_KEY"]
    return env


# ----------------------------------------------------------------------------- preparation
def prepare(args, scenarios):
    prep = args.out / "_prep"
    prep.mkdir(parents=True, exist_ok=True)
    usage = prep / "usage.jsonl"
    env = base_env(args) | {"VERIGUARD_USAGE_FILE": str(usage)}

    print("== benign datasets")
    subprocess.run([sys.executable, "-m", "veriguard.make_benign"], cwd=REPO, env=env, check=False)

    missing = [s for s in scenarios if not (args.spec_dir / f"scenario_{s}.md").exists()]
    if missing:
        print(f"== drafting {len(missing)} specs")
        subprocess.run([sys.executable, "-m", "veriguard.draft_specs", "-s", ",".join(missing),
                        "--model", args.vg_model, "--out", str(args.spec_dir)],
                       cwd=REPO, env=env, check=False)

    todo = [s for s in scenarios
            if (args.spec_dir / f"scenario_{s}.md").exists()
            and not (args.policy_dir / f"scenario_{s}.py").exists()]
    if todo:
        print(f"== generating {len(todo)} policies with {args.vg_model}")

    def gen(s):
        log = prep / f"generate_{s}.log"
        with open(log, "w", encoding="utf-8") as f:
            r = subprocess.run([sys.executable, "-m", "veriguard.generate", "-s", s,
                                "--model", args.vg_model], cwd=REPO, env=env,
                               stdout=f, stderr=subprocess.STDOUT)
        ok = (args.policy_dir / f"scenario_{s}.py").exists()
        print(f"  [{s}] policy {'ok' if ok else 'FAILED, see ' + str(log)} (exit {r.returncode})")

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        list(ex.map(gen, todo))

    p, c = read_usage(usage)
    print(f"== preparation spend so far: ${cost(args.vg_model, p, c):.3f}")


def preflight_benign(args):
    r = subprocess.run([sys.executable, str(RUNNER), "--list-attack-types"], cwd=REPO,
                       env=base_env(args), capture_output=True, text=True)
    return "benign" in (r.stdout + r.stderr)


# ----------------------------------------------------------------------------- one run
SUCC_RE = re.compile(r"Attack Success Rate:\s*([\d.]+)%\s*\((\d+)/(\d+)\)")
TOK_RE = re.compile(r"\[Token Stats\]\s*Prompt:\s*(\d+)\s*\|\s*Completion:\s*(\d+)")
TYPE_RE = re.compile(r"^\s{2}([\w.\-]+):\s*([\d.]+)%\s*\((\d+)/(\d+)\)\s*$")


def parse_stdout(text):
    out = {"succ": None, "total": None, "by_type": {}, "agent_prompt": 0, "agent_completion": 0}
    m = SUCC_RE.findall(text)
    if m:
        out["succ"], out["total"] = int(m[-1][1]), int(m[-1][2])
    for p, c in TOK_RE.findall(text):
        out["agent_prompt"] += int(p)
        out["agent_completion"] += int(c)
    in_types = False
    for line in text.splitlines():
        if line.strip().startswith("By Attack Type"):
            in_types = True
            continue
        if in_types:
            t = TYPE_RE.match(line)
            if t:
                out["by_type"][t.group(1)] = [int(t.group(3)), int(t.group(4))]
            elif line.strip():
                in_types = False
    return out


def parse_guard_log(path: Path):
    calls = blocks = errors = 0
    blocked_cases, seen_cases = set(), set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen_cases.add(row.get("case_id"))
            if row.get("reason", "").startswith("allowed on tool name alone"):
                continue
            calls += 1
            if row.get("error"):
                errors += 1
            if not row.get("allowed", True):
                blocks += 1
                blocked_cases.add(row.get("case_id"))
    return {"guard_calls": calls, "blocks": blocks, "guard_errors": errors,
            "blocked_cases": sorted(c for c in blocked_cases if c)}


def run_one(args, sid, arm, dataset, budget_state):
    d = args.out / sid
    d.mkdir(parents=True, exist_ok=True)
    tag = f"{arm}__{dataset}"
    summary_path = d / f"{tag}.summary.json"
    if summary_path.exists():
        return json.loads(summary_path.read_text(encoding="utf-8"))

    est = estimate_cost(args, arm, dataset)
    with _spent_lock:
        if budget_state["spent"] + budget_state["reserved"] + est > args.budget:
            budget_state["skipped"] += 1
            return None
        budget_state["reserved"] += est

    cfg = ARMS[arm]
    usage = d / f"{tag}.usage.jsonl"
    guard_log = d / f"{tag}.guard.jsonl"
    for f in (usage, guard_log):
        f.unlink(missing_ok=True)
    cmd = [sys.executable, str(RUNNER), "-s", sid, "--agent", args.agent,
           "--max-turns", str(args.max_turns), "-o", str(d / f"{tag}.json")]
    if dataset == "synthesis":
        cmd += ["--synthesis", "--attack-type", ",".join(SYNTH_TYPES)]
    elif dataset == "benign":
        cmd += ["--synthesis", "--attack-type", "benign"]
    if cfg["defense"]:
        cmd += ["--defense", cfg["defense"], "--vg-model", args.vg_model]
    env = base_env(args) | {"VERIGUARD_CONTEXT": cfg["context"],
                            "VERIGUARD_LOG_PATH": str(guard_log),
                            "VERIGUARD_USAGE_FILE": str(usage)}

    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True,
                           timeout=args.timeout)
        stdout, rc = r.stdout + "\n" + r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        stdout, rc = (e.stdout or "") if isinstance(e.stdout, str) else "", "timeout"
    (d / f"{tag}.stdout.txt").write_text(stdout, encoding="utf-8")

    parsed = parse_stdout(stdout)
    gp, gc = read_usage(usage)
    agent_model = AGENT_MODEL.get(args.agent, args.agent)
    run_cost = cost(agent_model, parsed["agent_prompt"], parsed["agent_completion"]) + cost(args.vg_model, gp, gc)
    summary = {"scenario": sid, "arm": arm, "dataset": dataset, "returncode": rc,
               "ok": rc == 0 and parsed["total"] is not None, "seconds": round(time.time() - t0, 1),
               **parsed, "guard_prompt": gp, "guard_completion": gc, "cost": round(run_cost, 4),
               **parse_guard_log(guard_log), "cmd": " ".join(cmd)}
    if summary["ok"]:
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    else:
        (d / f"{tag}.failed.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with _spent_lock:
        budget_state["spent"] += run_cost
        budget_state["reserved"] -= est
        spent = budget_state["spent"]
    status = "ok" if summary["ok"] else f"FAILED (exit {rc}, see {tag}.stdout.txt)"
    asr = f"{parsed['succ']}/{parsed['total']}" if parsed["total"] is not None else "-"
    print(f"[{sid}] {arm:15s} {dataset:9s} {status:6s} succ {asr:7s} blocks {summary['blocks']:3d} "
          f"${run_cost:.3f}  (total ${spent:.2f} / ${args.budget:.2f})", flush=True)
    return summary


DEFAULT_EST = {"original": 0.02, "benign": 0.02, "synthesis": 0.60}


def estimate_cost(args, arm, dataset):
    """Average cost of finished runs with the same arm and dataset; a cautious default otherwise."""
    costs = []
    for f in args.out.glob(f"*/{arm}__{dataset}.summary.json"):
        try:
            costs.append(json.loads(f.read_text(encoding="utf-8"))["cost"])
        except Exception:
            pass
    base = sum(costs) / len(costs) if costs else DEFAULT_EST.get(dataset, 0.5)
    return base * 1.5  # headroom: scenarios differ in size


def spent_so_far(args):
    total = 0.0
    for f in list(args.out.glob("*/*.summary.json")) + list(args.out.glob("*/*.failed.json")):
        try:
            total += json.loads(f.read_text(encoding="utf-8")).get("cost", 0)
        except Exception:
            pass
    p, c = read_usage(args.out / "_prep" / "usage.jsonl")
    return total + cost(args.vg_model, p, c)


# ----------------------------------------------------------------------------- report
def report(args):
    rows = [json.loads(f.read_text(encoding="utf-8")) for f in args.out.glob("*/*.summary.json")]
    if not rows:
        print("No finished runs yet.")
        return
    arms = [a for a in ARMS if any(r["arm"] == a for r in rows)]
    lines = []

    def table(title, header, body):
        widths = [max(len(str(x)) for x in col) for col in zip(header, *body)]
        lines.append(title)
        lines.append("  ".join(str(h).ljust(w) for h, w in zip(header, widths)))
        lines.append("  ".join("-" * w for w in widths))
        for b in body:
            lines.append("  ".join(str(x).ljust(w) for x, w in zip(b, widths)))
        lines.append("")

    def pct(a, b):
        return f"{100 * a / b:5.1f}% ({a}/{b})" if b else "-"

    # Table 1: ASR on attacks, false blocks on benign, per arm (paper Table 1 layout)
    body = []
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        o = [r for r in rs if r["dataset"] == "original"]
        s = [r for r in rs if r["dataset"] == "synthesis"]
        b = [r for r in rs if r["dataset"] == "benign"]
        b_total = sum(r["total"] for r in b)
        body.append([arm,
                     pct(sum(r["succ"] for r in o), sum(r["total"] for r in o)),
                     pct(sum(r["succ"] for r in s), sum(r["total"] for r in s)),
                     pct(sum(len(r["blocked_cases"]) for r in b), b_total),
                     pct(b_total - sum(len(r["blocked_cases"]) for r in b), b_total),
                     f"{len({r['scenario'] for r in rs})}",
                     f"${sum(r['cost'] for r in rs):.2f}"])
    table("ASR on attacks (lower is better); benign false-block rate (lower is better); "
          "benign pass rate = utility proxy (higher is better)",
          ["arm", "ASR original", "ASR synthesis", "benign blocked", "benign pass", "scen", "cost"], body)

    # Table 2: synthesis ASR by attack type
    types = sorted({t for r in rows if r["dataset"] == "synthesis" for t in r["by_type"]})
    body = []
    for t in types:
        line = [t]
        for arm in arms:
            rs = [r for r in rows if r["arm"] == arm and r["dataset"] == "synthesis" and t in r["by_type"]]
            line.append(pct(sum(r["by_type"][t][0] for r in rs), sum(r["by_type"][t][1] for r in rs)))
        body.append(line)
    if body:
        table("Synthesis ASR by attack type", ["attack type"] + arms, body)

    # Table 3: per scenario, original + synthesis combined
    body = []
    for sid in sorted({r["scenario"] for r in rows}):
        line = [sid]
        for arm in arms:
            rs = [r for r in rows if r["scenario"] == sid and r["arm"] == arm and r["dataset"] != "benign"]
            line.append(pct(sum(r["succ"] for r in rs), sum(r["total"] for r in rs)))
        body.append(line)
    table("ASR per scenario (original + synthesis)", ["scenario"] + arms, body)

    failed = list(args.out.glob("*/*.failed.json"))
    lines.append(f"Finished runs: {len(rows)}   failed runs: {len(failed)}   "
                 f"total spend: ${spent_so_far(args):.2f}")
    text = "\n".join(lines)
    (args.out / "report.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nSaved {args.out / 'report.txt'}")


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="VeriGuard vs baselines on FinVault")
    ap.add_argument("--scenarios", help="Comma-separated ids (default: all sandbox_XX)")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--datasets", default=",".join(DATASET_ORDER))
    ap.add_argument("--agent", default="uf_gpt_oss_120b", help="Runner agent name")
    ap.add_argument("--vg-model", default="gpt-oss-120b", help="Model for policies, extraction, guardrail")
    ap.add_argument("--max-turns", type=int, default=5)
    ap.add_argument("--jobs", type=int, default=3, help="Runs in parallel")
    ap.add_argument("--budget", type=float, default=20.0, help="Stop launching runs past this USD spend")
    ap.add_argument("--timeout", type=int, default=3600, help="Seconds per run")
    ap.add_argument("--out", default=None, help="Results dir (default results/suite_<agent>)")
    ap.add_argument("--prepare", action="store_true", help="Only build specs, benign data and policies")
    ap.add_argument("--report", action="store_true", help="Only print tables from finished runs")
    ap.add_argument("--estimate", action="store_true", help="Print the planned runs and estimated cost, run nothing")
    ap.add_argument("--force-benign", action="store_true",
                    help="Run benign even if the runner does not list it (it loads it from the synthesis folder)")
    args = ap.parse_args()

    args.out = Path(args.out or REPO / "results" / f"suite_{args.agent}")
    args.spec_dir = ROOT / "specs_auto"
    args.policy_dir = ROOT / f"policies_{args.vg_model}"
    args.out.mkdir(parents=True, exist_ok=True)

    if args.report:
        report(args)
        return
    scenarios = [s.strip().zfill(2) for s in args.scenarios.split(",")] if args.scenarios else all_scenarios()
    prepare(args, scenarios)
    if args.prepare:
        return

    datasets = [d for d in DATASET_ORDER if d in args.datasets.split(",")]
    if "benign" in datasets and not args.force_benign and not preflight_benign(args):
        print("WARNING: the runner does not list 'benign' as an attack type; skipping benign runs. "
              "Tell Claude the output of: python sandbox/run_attack_test.py --list-attack-types")
        datasets.remove("benign")

    arms = [a for a in ARMS if a in args.arms.split(",")]
    jobs = []
    for ds in datasets:  # cheap, informative datasets first so a partial run is still useful
        for sid in scenarios:
            for arm in arms:
                if ARMS[arm]["defense"] == "veriguard" and not (args.policy_dir / f"scenario_{sid}.py").exists():
                    continue
                if ARMS[arm]["defense"] == "guardrail" and not (args.spec_dir / f"scenario_{sid}.md").exists():
                    continue
                if not (args.out / sid / f"{arm}__{ds}.summary.json").exists():
                    jobs.append((sid, arm, ds))

    if args.estimate:
        by = defaultdict(lambda: [0, 0.0])
        for sid, arm, ds in jobs:
            by[(arm, ds)][0] += 1
            by[(arm, ds)][1] += estimate_cost(args, arm, ds) / 1.5
        total = 0.0
        for (arm, ds), (n, c) in sorted(by.items()):
            total += c
            print(f"  {arm:15s} {ds:9s} {n:3d} runs  ~${c:6.2f}")
        print(f"== {len(jobs)} runs, estimated ~${total:.2f} (from finished-run averages; "
              f"spent so far ${spent_so_far(args):.2f})")
        return
    budget_state = {"spent": spent_so_far(args), "reserved": 0.0, "skipped": 0}
    print(f"== {len(jobs)} runs still to do; spent so far ${budget_state['spent']:.2f} of ${args.budget:.2f}")
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = [ex.submit(run_one, args, sid, arm, ds, budget_state) for sid, arm, ds in jobs]
        for _ in as_completed(futures):
            pass
    if budget_state["skipped"]:
        print(f"== {budget_state['skipped']} runs not started to stay under ${args.budget:.2f} "
              f"(spent ${budget_state['spent']:.2f}); rerun with a higher --budget to continue")
    report(args)


if __name__ == "__main__":
    main()