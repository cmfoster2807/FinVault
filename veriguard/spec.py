"""Load the paper's inputs for a scenario: the security request r and agent specification S."""
import importlib
import json
import re
from pathlib import Path

import os
SPEC_DIR = Path(os.environ.get("VERIGUARD_SPEC_DIR") or Path(__file__).resolve().parent / "specs")


def norm_id(scenario_id) -> str:
    digits = re.sub(r"\D", "", str(scenario_id))
    if not digits:
        raise ValueError(f"bad scenario id: {scenario_id!r}")
    return f"{int(digits):02d}"


def _section(text: str, title: str) -> str:
    m = re.search(rf"^##\s+{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


def _tool_definitions(sid: str) -> str:
    try:
        mod = importlib.import_module(f"sandbox.sandbox_{sid}.scenario")
    except Exception as e:  # scenario imports vary; fall back to the hand-written spec
        return f"(could not import sandbox_{sid}.scenario: {e!r}; describe tools in the spec)"
    fn = getattr(mod, "get_tools", None)
    if not callable(fn):
        return "(no get_tools() in scenario.py; describe tools in the spec)"
    try:
        return json.dumps(fn(), indent=2, ensure_ascii=False, default=str)
    except Exception as e:
        return f"(get_tools() failed: {e!r})"


def load_spec(scenario_id) -> dict:
    sid = norm_id(scenario_id)
    path = SPEC_DIR / f"scenario_{sid}.md"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; copy specs/TEMPLATE.md and fill it in")
    text = path.read_text(encoding="utf-8")
    request = _section(text, "Security request")
    agent = _section(text, "Agent specification")
    if not request:
        raise ValueError(f"{path}: '## Security request' section is empty")
    tools = _tool_definitions(sid)
    agent_spec = f"{agent}\n\nTool definitions:\n{tools}"
    # The paper's agent specification S includes the agent's I/O data structures. scenario.py often
    # lists tools without their parameters, so also include the agent's own instructions (which
    # document each tool's parameters and return fields), with vulnerability lines removed.
    # VERIGUARD_SPEC_TOOLDOCS=0 restores the original behaviour.
    if os.environ.get("VERIGUARD_SPEC_TOOLDOCS", "1") != "0":
        try:
            from .draft_specs import scenario_instructions
            agent_spec += ("\n\nAgent instructions (tool parameters and return fields; this is the only "
                           "data available to the agent and the policy):\n" + scenario_instructions(sid))
        except Exception as e:
            agent_spec += f"\n\n(agent instructions unavailable: {e!r})"
    return {
        "scenario_id": sid,
        "path": str(path),
        "request": request,
        "agent_spec": agent_spec,
    }
