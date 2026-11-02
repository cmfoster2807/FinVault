"""Draft a VeriGuard spec (security request r + agent specification S) for every scenario.

Every spec is drafted the same way: one fixed instruction (prompts.SPEC_DRAFT) applied to the
scenario's base system prompt from sandbox/prompts/prompt_XX.py. The vulnerability summary and any
line mentioning vulnerabilities are removed first, and vulnerability.py is never read, so the
grader cannot leak into the rules.

Run from the repo root:
  python -m veriguard.draft_specs --model gpt-oss-120b              # all scenarios, skip existing
  python -m veriguard.draft_specs -s 02,13 --model gpt-oss-120b --force
Writes veriguard/specs_auto/scenario_XX.md (override with --out or VERIGUARD_SPEC_DIR).
"""
import argparse
import importlib.util
import os
import re
import time
from pathlib import Path

from . import prompts
from .llm import DEFAULT_MODEL, chat
from .spec import norm_id

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
PROMPT_DIR = REPO / "sandbox" / "prompts"
MAX_CHARS = 14000


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"vg_prompt_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scenario_instructions(sid: str) -> str:
    """Base system prompt for a scenario, with anything about vulnerabilities removed."""
    path = PROMPT_DIR / f"prompt_{sid}.py"
    if not path.exists():
        raise FileNotFoundError(path)
    text = None
    try:
        mod = _load_module(path)
        for name in ("SYSTEM_PROMPT", "BASE_SYSTEM_PROMPT", "AGENT_SYSTEM_PROMPT"):
            if isinstance(getattr(mod, name, None), str):
                text = getattr(mod, name)
                break
        if text is None and callable(getattr(mod, "get_system_prompt", None)):
            text = mod.get_system_prompt()
    except Exception:
        text = None
    if not text:  # fall back to the raw file with the vulnerability table removed
        raw = path.read_text(encoding="utf-8")
        raw = re.sub(r"VULNERABILIT\w*_SUMMARY\s*=\s*(\"\"\"|''').*?\1", "", raw, flags=re.S)
        text = raw
    kept = [ln for ln in text.splitlines() if not re.search(r"vulnerab|漏洞", ln, re.I)]
    return "\n".join(kept)[:MAX_CHARS]


def main():
    ap = argparse.ArgumentParser(description="Draft VeriGuard specs from scenario system prompts")
    ap.add_argument("--scenario", "-s", help="Scenario id(s), comma-separated (default: all)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", default=os.environ.get("VERIGUARD_SPEC_DIR") or str(ROOT / "specs_auto"))
    ap.add_argument("--force", action="store_true", help="Overwrite existing drafts")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.scenario:
        ids = [norm_id(s) for s in args.scenario.split(",") if s.strip()]
    else:
        ids = sorted(norm_id(p.stem.split("_")[1]) for p in PROMPT_DIR.glob("prompt_*.py"))

    for sid in ids:
        dest = out / f"scenario_{sid}.md"
        if dest.exists() and not args.force:
            print(f"[{sid}] exists, skipped")
            continue
        try:
            instructions = scenario_instructions(sid)
            body = chat(prompts.SPEC_DRAFT.format(instructions=instructions), model=args.model)
        except Exception as e:
            print(f"[{sid}] FAILED: {e!r}")
            continue
        if "## Security request" not in body or "## Agent specification" not in body:
            print(f"[{sid}] FAILED: draft is missing a required section; rerun with --force")
            continue
        body = body[body.index("## Security request"):].strip()
        header = (f"# Scenario {sid} - VeriGuard spec (auto-drafted)\n\n"
                  f"<!-- Drafted {time.strftime('%Y-%m-%d %H:%M')} with {args.model} by "
                  f"veriguard/draft_specs.py from sandbox/prompts/prompt_{sid}.py (base system prompt, "
                  f"vulnerability lines removed). Review before use; keep fixed across all runs. -->\n\n")
        dest.write_text(header + body + "\n", encoding="utf-8")
        print(f"[{sid}] wrote {dest}")


if __name__ == "__main__":
    main()
