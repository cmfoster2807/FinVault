"""Offline stage: generate -> validate -> test (-> verify) one policy per scenario.

Run from the repo root:
  python -m veriguard.generate --scenario 02 --dry-run      # check the spec loads, no LLM calls
  python -m veriguard.generate --scenario 02,13 --model <id>
  python -m veriguard.generate --scenario 02 --verify       # add the Nagini stage

Writes veriguard/policies/scenario_XX.py (used at runtime) and a full trace of every
intermediate artifact to veriguard/runs/ for reproducibility.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import prompts
from .llm import DEFAULT_MODEL, chat
from .spec import load_spec
from .util import check_policy_code, extract_code
from .verify_nagini import verify as nagini_verify

ROOT = Path(__file__).resolve().parent
POLICY_DIR = Path(os.environ.get("VERIGUARD_POLICY_DIR") or ROOT / "policies")
RUN_DIR = ROOT / "runs"


def gen_policy(spec, model, requirements="", previous="", attempts=3) -> str:
    prev = f"Previous version to improve:\n```python\n{previous}\n```\n" if previous else ""
    last_err = None
    for _ in range(attempts):
        reply = chat(prompts.POLICY_GEN.format(
            spec=spec["agent_spec"], request=spec["request"],
            requirements=requirements or "(none)", previous=prev), model=model)
        try:
            code = extract_code(reply)
            check_policy_code(code)
            return code
        except Exception as e:
            last_err = e
            prev += f"\nYour last output was rejected: {e!r}. Fix that.\n"
    raise RuntimeError(f"policy generation failed {attempts} times: {last_err!r}")


def gen_constraints(spec, model, code, requirements="") -> str:
    return chat(prompts.CONSTRAINTS_GEN.format(
        request=spec["request"], spec=spec["agent_spec"], code=code,
        requirements=requirements or "(none)"), model=model)


def run_tests(code: str, tests: str, timeout: int = 120) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as d:
        Path(d, "policy_under_test.py").write_text(code, encoding="utf-8")
        Path(d, "test_policy.py").write_text(tests, encoding="utf-8")
        try:
            p = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
                 "test_policy.py"],
                cwd=d, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"pytest timed out after {timeout}s"
    return p.returncode == 0, (p.stdout + p.stderr)[-4000:]


def synthesize(scenario_id, model, max_iters, verify) -> dict:
    spec = load_spec(scenario_id)
    sid = spec["scenario_id"]
    trace = {"scenario_id": sid, "model": model, "spec_path": spec["path"],
             "request": spec["request"], "max_iters": max_iters,
             "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "steps": []}

    def note(stage, **kw):
        trace["steps"].append({"stage": stage, **kw})
        flag = kw.get("passed")
        print(f"[{sid}] {stage}" + ("" if flag is None else f" passed={flag}"), flush=True)

    # Iteration 1: initial generation, then validation (autonomous disambiguation).
    code = gen_policy(spec, model)
    constraints = gen_constraints(spec, model, code)
    note("generate", code=code, constraints=constraints)

    analysis = chat(prompts.VALIDATION_ANALYSIS.format(
        request=spec["request"], constraints=constraints), model=model)
    requirements = ""
    if analysis.strip().upper() != "NONE":
        requirements = chat(prompts.DISAMBIGUATE.format(
            request=spec["request"], spec=spec["agent_spec"], analysis=analysis), model=model)
        code = gen_policy(spec, model, requirements=requirements, previous=code)
        constraints = gen_constraints(spec, model, code, requirements)
    note("validate", analysis=analysis, requirements=requirements, code=code,
         constraints=constraints)

    # Iteration 2: generated unit tests, fix until they pass or the cap is hit.
    tests = extract_code(chat(prompts.TEST_GEN.format(
        request=spec["request"], requirements=requirements or "(none)", code=code), model=model))
    tests_passed = False
    for i in range(max_iters):
        tests_passed, out = run_tests(code, tests)
        note("test", iteration=i + 1, passed=tests_passed, output=out)
        if tests_passed:
            break
        try:
            fixed = extract_code(chat(prompts.FIX_FROM_TESTS.format(
                request=spec["request"], requirements=requirements or "(none)",
                code=code, tests=tests, output=out), model=model))
            check_policy_code(fixed)
            code = fixed
        except Exception as e:
            note("fix_rejected", error=repr(e))

    # Iteration 3 (optional): Nagini contracts + static verification.
    verified = None
    if verify:
        verified = False
        feedback = ""
        for i in range(max_iters):
            annotated = extract_code(chat(prompts.NAGINI_ANNOTATE.format(
                constraints=constraints, code=code, feedback=feedback), model=model))
            ok, out = nagini_verify(annotated)
            note("verify", iteration=i + 1, passed=ok, output=out, annotated=annotated)
            if ok:
                # Only adopt the annotated file if the logic still passes the same tests.
                same, tout = run_tests(annotated, tests)
                note("verify_regression", passed=same, output=tout)
                verified = True
                if same:
                    code = annotated
                break
            feedback = f"Nagini rejected the previous attempt:\n{out}\nFix the contracts."

    POLICY_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    header = (f"# VeriGuard policy for FinVault scenario {sid}\n"
              f"# generated {stamp} with {model}; tests_passed={tests_passed} verified={verified}\n"
              f"# trace: veriguard/runs/scenario_{sid}_{stamp}.json. Regenerate, don't hand-edit.\n")
    (POLICY_DIR / f"scenario_{sid}.py").write_text(header + code, encoding="utf-8")
    trace.update(final_code=code, constraints=constraints, requirements=requirements,
                 tests=tests, tests_passed=tests_passed, verified=verified,
                 finished=time.strftime("%Y-%m-%dT%H:%M:%S"))
    (RUN_DIR / f"scenario_{sid}_{stamp}.json").write_text(
        json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[{sid}] saved policies/scenario_{sid}.py  tests_passed={tests_passed} "
          f"verified={verified}", flush=True)
    return trace


def main():
    ap = argparse.ArgumentParser(description="Generate VeriGuard policies for FinVault scenarios")
    ap.add_argument("--scenario", "-s", required=True, help="Scenario id(s), comma-separated")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Model id for every generation stage")
    ap.add_argument("--max-iters", type=int, default=3, help="Cap on test and verify loops (N)")
    ap.add_argument("--verify", action="store_true", help="Run the Nagini verification stage")
    ap.add_argument("--dry-run", action="store_true", help="Load and print specs only; no LLM calls")
    args = ap.parse_args()

    for sid in [s.strip() for s in args.scenario.split(",") if s.strip()]:
        if args.dry_run:
            spec = load_spec(sid)
            print(f"=== scenario {spec['scenario_id']} ({spec['path']}) ===")
            print("REQUEST:\n" + spec["request"])
            print("\nAGENT SPEC:\n" + spec["agent_spec"][:3000])
            continue
        synthesize(sid, args.model, args.max_iters, args.verify)


if __name__ == "__main__":
    main()
