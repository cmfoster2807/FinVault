"""Online stage: check each proposed tool call before it reaches the environment.

Two defenses share one interface (check / record / blocked_observation):
  veriguard  the generated policy decides; an LLM extracts the policy's arguments (the paper's method)
  guardrail  an LLM judges compliance directly from the same inputs (the paper's GuardRail baseline)

On a deny, the call is never passed to env.step(); the agent instead gets an observation
saying the tool did not run and why (Tool Execution Halt + Collaborative Re-planning).

Extractor / judge input is controlled by VERIGUARD_CONTEXT (or configure(context_mode=...)):
  full     (default, the paper's setting) proposed call + executed tool history + conversation
  history  proposed call + executed tool history only; untrusted conversation text withheld
"""
import importlib.util
import json
import os
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import prompts
from .llm import chat
from .spec import load_spec, norm_id
from .util import extract_json

ROOT = Path(__file__).resolve().parent
POLICY_DIR = Path(os.environ.get("VERIGUARD_POLICY_DIR") or ROOT / "policies")
LOG_DIR = ROOT / "logs"

CONFIG = {"enabled": False, "defense": "veriguard", "model": None, "fail_mode": "closed",
          "log_path": os.environ.get("VERIGUARD_LOG_PATH"),
          "context_chars": 2500, "history_items": 4,
          "context_mode": os.environ.get("VERIGUARD_CONTEXT", "full")}
_CACHE: dict = {}
_LOCK = threading.Lock()
_LOG_LOCK = threading.Lock()


@dataclass
class Decision:
    allowed: bool
    reason: str
    tool: str = ""
    arguments: dict = field(default_factory=dict)
    error: str = ""


def configure(**kwargs) -> None:
    CONFIG.update({k: v for k, v in kwargs.items() if v is not None})
    if CONFIG["context_mode"] not in ("full", "history"):
        raise ValueError("context_mode must be 'full' or 'history'")
    if CONFIG["defense"] not in ("veriguard", "guardrail"):
        raise ValueError("defense must be 'veriguard' or 'guardrail'")
    if CONFIG["enabled"] and not CONFIG["log_path"]:
        LOG_DIR.mkdir(exist_ok=True)
        CONFIG["log_path"] = str(LOG_DIR / f"{CONFIG['defense']}_{time.strftime('%Y%m%d-%H%M%S')}.jsonl")


def get_veriguard(scenario_id):
    """Return the cached guard for a scenario, or None when no defense is enabled."""
    if not CONFIG["enabled"]:
        return None
    sid = norm_id(scenario_id)
    with _LOCK:
        if sid not in _CACHE:
            _CACHE[sid] = GuardRail(sid) if CONFIG["defense"] == "guardrail" else VeriGuard(sid)
        return _CACHE[sid]


def _parse_tool_call(action_str):
    try:
        parsed = json.loads(action_str)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict) or "tool" not in parsed:
        return None
    return str(parsed["tool"])


class _Guard:
    """Shared episode history, blocked observation and logging."""

    def __init__(self, scenario_id):
        self.sid = norm_id(scenario_id)
        self._history = defaultdict(list)  # case_id -> executed calls and results

    def record(self, case_id, action_str: str, observation) -> None:
        """Call after every executed env.step() so later checks can see episode history."""
        obs = observation if isinstance(observation, str) else json.dumps(
            observation, ensure_ascii=False, default=str)
        self._history[case_id].append(f"CALL {action_str[:800]}\nRESULT {obs[:1200]}")

    def _inputs(self, case_id, context):
        history = "\n\n".join(self._history[case_id][-CONFIG["history_items"]:]) or "(none)"
        if CONFIG["context_mode"] == "history":
            ctx = "(withheld: only executed tool history is available)"
        else:
            ctx = context[-CONFIG["context_chars"]:]
        return history, ctx

    @staticmethod
    def blocked_observation(decision: Decision) -> dict:
        return {
            "status": "blocked",
            "tool": decision.tool,
            "error": "Tool was not executed; no result is available.",
            "policy_violation": decision.reason,
            "instruction": "This action violates a security policy. Choose a different action "
                           "that complies with it, or escalate to a human.",
        }

    def _fail(self, tool, e):
        allowed = CONFIG["fail_mode"] == "open"
        return Decision(allowed, f"{CONFIG['defense']} error, fail-{CONFIG['fail_mode']}",
                        tool, {}, repr(e))

    def _log(self, decision, action_str, case_id, seconds, history, context):
        path = CONFIG["log_path"]
        if not path:
            return
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "defense": CONFIG["defense"],
               "scenario": self.sid, "case_id": case_id, "context_mode": CONFIG["context_mode"],
               "action": action_str[:1000], "seconds": round(seconds, 2), **asdict(decision),
               # what the extractor / judge actually saw, so any decision can be traced to its source
               "history_seen": history[-3000:], "context_seen": context[-3000:]}
        with _LOG_LOCK, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


class VeriGuard(_Guard):
    def __init__(self, scenario_id):
        super().__init__(scenario_id)
        path = POLICY_DIR / f"scenario_{self.sid}.py"
        if not path.exists():
            raise FileNotFoundError(
                f"No policy at {path}. Run: python -m veriguard.generate -s {self.sid}")
        self.source = path.read_text(encoding="utf-8")
        mod_spec = importlib.util.spec_from_file_location(f"vg_policy_{self.sid}", path)
        mod = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
        self.policy = mod.policy
        self.arg_spec = mod.list_of_arguments_to_read

    def check(self, action_str: str, context: str = "", case_id=None) -> Decision:
        tool = _parse_tool_call(action_str)
        if tool is None:
            return Decision(True, "not a tool call")
        history, ctx = self._inputs(case_id, context)

        # Tools the policy allows on their name alone need no extraction (cost saving).
        try:
            probe_ok, _ = self.policy({"tool_name": tool})
        except Exception:
            probe_ok = False
        if probe_ok:
            d = Decision(True, "allowed on tool name alone, no extraction", tool, {"tool_name": tool})
            self._log(d, action_str, case_id, 0.0, history, "")
            return d

        t0 = time.time()
        try:
            reply = chat(prompts.EXTRACT.format(
                policy_source=self.source,
                arg_spec=json.dumps({k: getattr(v, "__name__", str(v))
                                     for k, v in self.arg_spec.items()}),
                action=action_str, history=history, context=ctx), model=CONFIG["model"])
            arguments = extract_json(reply)
            arguments["tool_name"] = tool  # tool name is unambiguous, so take it from the call itself
            allowed, reason = self.policy(arguments)
            decision = Decision(bool(allowed), str(reason), tool, arguments)
        except Exception as e:
            decision = self._fail(tool, e)
        self._log(decision, action_str, case_id, time.time() - t0, history, ctx)
        return decision


class GuardRail(_Guard):
    """The paper's GuardRail baseline: same inputs as VeriGuard, LLM judges compliance directly."""

    def __init__(self, scenario_id):
        super().__init__(scenario_id)
        self.spec = load_spec(self.sid)

    def check(self, action_str: str, context: str = "", case_id=None) -> Decision:
        tool = _parse_tool_call(action_str)
        if tool is None:
            return Decision(True, "not a tool call")
        history, ctx = self._inputs(case_id, context)
        t0 = time.time()
        try:
            reply = chat(prompts.GUARDRAIL_JUDGE.format(
                request=self.spec["request"], spec=self.spec["agent_spec"],
                action=action_str, history=history, context=ctx), model=CONFIG["model"])
            verdict = extract_json(reply)
            decision_word = str(verdict.get("decision", "")).strip().lower()
            if decision_word not in ("allow", "deny"):
                raise ValueError(f"unexpected decision {decision_word!r}")
            decision = Decision(decision_word == "allow", str(verdict.get("reason", "")), tool)
        except Exception as e:
            decision = self._fail(tool, e)
        self._log(decision, action_str, case_id, time.time() - t0, history, ctx)
        return decision
