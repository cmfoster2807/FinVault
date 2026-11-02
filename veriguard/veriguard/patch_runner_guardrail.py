"""Second runner patch: add --defense guardrail. Run once from the repo root, after patch_runner.py:
  python -m veriguard.patch_runner_guardrail
"""
import py_compile
import re
import shutil
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "sandbox" / "run_attack_test.py"
src = TARGET.read_text(encoding="utf-8")

if "'guardrail'" in src:
    print("already patched")
    sys.exit(0)
if "veriguard_enforce" not in src:
    sys.exit("run patch_runner.py first")

old_choice = "choices=['none', 'veriguard']"
if src.count(old_choice) != 1:
    sys.exit("could not find the --defense choices")
src = src.replace(old_choice, "choices=['none', 'veriguard', 'guardrail']")

m = re.search(r"^(\s*)if args\.defense == 'veriguard':\n(\s*)veriguard_enforce\.configure\(enabled=True, "
              r"model=args\.vg_model, fail_mode=args\.vg_fail_mode\)", src, re.M)
if not m:
    sys.exit("could not find the configure() block")
src = src.replace(m.group(0),
                  f"{m.group(1)}if args.defense in ('veriguard', 'guardrail'):\n"
                  f"{m.group(2)}veriguard_enforce.configure(enabled=True, defense=args.defense, "
                  f"model=args.vg_model, fail_mode=args.vg_fail_mode)")

backup = TARGET.with_suffix(".py.bak2")
shutil.copy2(TARGET, backup)
TARGET.write_text(src, encoding="utf-8")
try:
    py_compile.compile(str(TARGET), doraise=True)
except py_compile.PyCompileError as e:
    shutil.copy2(backup, TARGET)
    sys.exit(f"patched file did not compile, original restored:\n{e}")
print(f"Patched {TARGET}: --defense now accepts guardrail (backup {backup.name})")
