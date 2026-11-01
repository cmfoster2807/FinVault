"""Wire VeriGuard into sandbox/run_attack_test.py.

Run from the FinVault repo root:  python patch_runner.py
Makes a backup at sandbox/run_attack_test.py.bak, applies four edits, and compile-checks the result.
With --defense none (the default) the runner behaves exactly as before.
"""
import py_compile
import re
import shutil
import sys
from pathlib import Path

TARGET = Path("sandbox/run_attack_test.py")
MARK = "veriguard_enforce"


def fail(msg):
    print(f"ABORTED, nothing written: {msg}")
    sys.exit(1)


src = TARGET.read_text(encoding="utf-8")
if MARK in src:
    fail("file already contains VeriGuard edits")
lines = src.split("\n")

# --- find the env.step call in the case loop ---------------------------------
step_re = re.compile(r"^(\s*)new_obs, reward, terminated, truncated, step_info = env\.step\(action_str\)\s*$")
step_idx = [i for i, l in enumerate(lines) if step_re.match(l)]
if len(step_idx) != 1:
    fail(f"expected exactly one 'env.step(action_str)' line, found {len(step_idx)}")
step_i = step_idx[0]
indent = step_re.match(lines[step_i]).group(1)

# --- work out the scenario-id variable in the enclosing function ------------
def_i = next((i for i in range(step_i, -1, -1) if re.match(r"^\s*(async\s+)?def\s", lines[i])), None)
if def_i is None:
    fail("could not find the function that contains env.step")
sig_end = def_i
while not lines[sig_end].rstrip().endswith(":") and sig_end < step_i:
    sig_end += 1
signature = " ".join(l.strip() for l in lines[def_i:sig_end + 1])
body = "\n".join(lines[def_i:step_i])
candidates = [n for n in ("scenario_id", "scenario", "sid") if re.search(rf"\b{n}\b", signature)]
if not candidates:
    candidates = [n for n in ("scenario_id",) if re.search(rf"^\s*{n}\s*=", body, re.M)]
add_param = False
if not candidates:
    # FinVault: _run_single_case(self, env, agent, case, max_turns) is called from a method that
    # has scenario_id in scope, so thread it through as a new keyword argument.
    if "_run_single_case" not in signature:
        fail(f"no scenario-id variable found in: {signature}\nTell Claude this signature.")
    calls = [i for i, l in enumerate(lines)
             if "self._run_single_case(env, agent, case, max_turns)" in l]
    if len(calls) != 1:
        fail(f"expected one call 'self._run_single_case(env, agent, case, max_turns)', found {len(calls)}")
    add_param = True
    candidates = ["scenario_id"]
scen = candidates[0]

# --- edit 4: hook around env.step --------------------------------------------
i4 = indent
hook = [
    f"{i4}vg = veriguard_enforce.get_veriguard({scen})",
    f"{i4}if vg is not None:",
    f"{i4}    vg_context = f\"User turns:\\n{{context_with_history}}\\n\\nLatest observation:\\n{{obs_str}}\"",
    f"{i4}    decision = vg.check(action_str, context=vg_context, case_id=case_id)",
    f"{i4}    if not decision.allowed:",
    f"{i4}        turn_interactions += 1",
    f"{i4}        turns.append({{",
    f"{i4}            \"multi_turn\": turn_idx + 1, \"interaction\": interaction + 1,",
    f"{i4}            \"action\": action_str[:500], \"reward\": 0, \"vulnerabilities\": [],",
    f"{i4}            \"blocked_by\": \"veriguard\", \"block_reason\": decision.reason,",
    f"{i4}        }})",
    f"{i4}        obs = vg.blocked_observation(decision)",
    f"{i4}        continue",
    lines[step_i],
    f"{i4}if vg is not None:",
    f"{i4}    vg.record(case_id, action_str, new_obs)",
]
lines[step_i:step_i + 1] = hook

# --- thread scenario_id into _run_single_case when it isn't already a parameter ---
if add_param:
    d = next(i for i, l in enumerate(lines) if re.match(r"^\s*def\s+_run_single_case\s*\(", l))
    e = d
    while not lines[e].rstrip().endswith(":"):
        e += 1
    if d == e:  # one-line signature
        lines[d] = re.sub(r"\)\s*(->[^:]*)?:\s*$", r", scenario_id: str = None) \1:", lines[d]).replace(" ) ", ") ")
    else:       # multi-line: closing ')' is on line e
        prev = e - 1
        while not lines[prev].strip():
            prev -= 1
        if lines[prev].strip().startswith("(") or lines[prev].rstrip().endswith("("):
            fail("unexpected _run_single_case signature layout")
        if not lines[prev].rstrip().endswith(","):
            lines[prev] = lines[prev].rstrip() + ","
        p_indent = re.match(r"^(\s*)", lines[prev]).group(1)
        lines.insert(e, f"{p_indent}scenario_id: str = None")
    c = next(i for i, l in enumerate(lines) if "self._run_single_case(env, agent, case, max_turns)" in l)
    lines[c] = lines[c].replace("self._run_single_case(env, agent, case, max_turns)",
                                "self._run_single_case(env, agent, case, max_turns, scenario_id=scenario_id)")

# --- edit 3: configure after parse_args --------------------------------------
pa = [i for i, l in enumerate(lines) if re.match(r"^\s*args\s*=\s*parser\.parse_args\(\)", l)]
if len(pa) != 1:
    fail(f"expected one 'args = parser.parse_args()', found {len(pa)}")
pa_indent = re.match(r"^(\s*)", lines[pa[0]]).group(1)
lines[pa[0] + 1:pa[0] + 1] = [
    f"{pa_indent}if args.defense == 'veriguard':",
    f"{pa_indent}    veriguard_enforce.configure(enabled=True, model=args.vg_model, fail_mode=args.vg_fail_mode)",
]

# --- edit 2: CLI flags --------------------------------------------------------
fl = [i for i, l in enumerate(lines) if "add_argument('--list-scenarios'" in l]
if len(fl) != 1:
    fail("could not find the --list-scenarios argument to anchor the new flags")
fl_indent = re.match(r"^(\s*)", lines[fl[0]]).group(1)
lines[fl[0] + 1:fl[0] + 1] = [
    f"{fl_indent}parser.add_argument('--defense', choices=['none', 'veriguard'], default='none', help='Runtime defense')",
    f"{fl_indent}parser.add_argument('--vg-model', type=str, default=None, help='Model for VeriGuard argument extraction')",
    f"{fl_indent}parser.add_argument('--vg-fail-mode', choices=['closed', 'open'], default='closed', help='VeriGuard behaviour on errors')",
]

# --- edit 1: imports ----------------------------------------------------------
ap = [i for i, l in enumerate(lines) if re.match(r"^import argparse\s*$", l)]
if not ap:
    fail("could not find top-level 'import argparse'")
lines[ap[0] + 1:ap[0] + 1] = [
    "import sys as _vg_sys",
    "from pathlib import Path as _VgPath",
    "_vg_sys.path.insert(0, str(_VgPath(__file__).resolve().parent.parent))  # repo root, for veriguard",
    "from veriguard import enforce as veriguard_enforce",
]

# --- write, compile-check, roll back on failure -------------------------------
backup = TARGET.with_suffix(".py.bak")
shutil.copy2(TARGET, backup)
TARGET.write_text("\n".join(lines), encoding="utf-8")
try:
    py_compile.compile(str(TARGET), doraise=True)
except py_compile.PyCompileError as e:
    shutil.copy2(backup, TARGET)
    fail(f"patched file did not compile, original restored:\n{e}")

print(f"Patched {TARGET} (backup at {backup})")
print(f"  scenario-id variable used: {scen}   (enclosing function: {signature[:120]})")
print("  hook inserted at the env.step call; flags: --defense, --vg-model, --vg-fail-mode")