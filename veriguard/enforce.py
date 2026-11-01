"""Online stage: check each proposed tool call against the scenario's generated policy.

On a deny, the call is never passed to env.step(); the agent instead gets an observation
saying the tool did not run and why (Tool Execution Halt + Collaborative Re-planning).
"""
import importlib.util
import json
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import prompts
from .llm import chat
from .spec import norm_id
from .util import extract_json

ROOT = Path(__file__).resolve().parent
POLICY_DIR = ROOT / "policies"
LOG_DIR = ROOT / "logs"

CONFIG = {"enabled": False, "model": None, "fail_mode": "closed", "log_path": None,
          "context_chars": 6000, "history_items": 8}
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
    if CONFIG["enabled"] and not CONFIG["log_path"]:
        LOG_DIR.mkdir(exist_ok=True)
        CONFIG["log_path"] = str(LOG_DIR / f"veriguard_{time.strftime('%Y%m%d-%H%M%S')}.jsonl")


def get_veriguard(scenario_id):
    """Return the cached guard for a scenario, or None when VeriGuard is off."""
    if not CONFIG["enabled"]:
        return None
    sid = norm_id(scenario_id)
    with _LOCK:
        if sid not in _CACHE:
            _CACHE[sid] = VeriGuard(sid)
        return _CACHE[sid]


class VeriGuard:
    def __init__(self, scenario_id):
        self.sid = norm_id(scenario_id)
        path = POLICY_DIR / f"scenario_{self.sid}.py"
        if not path.exists():
            raise FileNotFoundError(
                f"No policy for scenario {self.sid}. Run: python -m veriguard.generate -s {self.sid}")
        self.source = path.read_text(encoding="utf-8")
        mod_spec = importlib.util.spec_from_file_location(f"vg_policy_{self.sid}", path)
        mod = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
        self.policy = mod.policy
        self.arg_spec = mod.list_of_arguments_to_read
        self._history = defaultdict(list)  # case_id -> executed calls and results

    def record(self, case_id, action_str: str, observation) -> None:
        """Call after every executed env.step() so later checks can see episode history."""
        obs = observation if isinstance(observation, str) else json.dumps(
            observation, ensure_ascii=False, default=str)
        self._history[case_id].append(f"CALL {action_str[:800]}\nRESULT {obs[:1200]}")

    def check(self, action_str: str, context: str = "", case_id=None) -> Decision:
        try:
            parsed = json.loads(action_str)
        except (json.JSONDecodeError, TypeError):
            return Decision(True, "not a tool call")
        if not isinstance(parsed, dict) or "tool" not in parsed:
            return Decision(True, "not a tool call")

        tool = str(parsed["tool"])
        history = "\n\n".join(self._history[case_id][-CONFIG["history_items"]:]) or "(none)"
        t0 = time.time()
        try:
            reply = chat(prompts.EXTRACT.format(
                policy_source=self.source,
                arg_spec=json.dumps({k: getattr(v, "__name__", str(v))
                                     for k, v in self.arg_spec.items()}),
                action=action_str,
                history=history,
                context=context[-CONFIG["context_chars"]:]), model=CONFIG["model"])
            arguments = extract_json(reply)
            arguments["tool_name"] = tool  # tool name is unambiguous, so take it from the call itself
            allowed, reason = self.policy(arguments)
            decision = Decision(bool(allowed), str(reason), tool, arguments)
        except Exception as e:
            allowed = CONFIG["fail_mode"] == "open"
            decision = Decision(allowed, f"VeriGuard error, fail-{CONFIG['fail_mode']}",
                                tool, {}, repr(e))
        self._log(decision, action_str, case_id, time.time() - t0)
        return decision

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

    def _log(self, decision, action_str, case_id, seconds):
        path = CONFIG["log_path"]
        if not path:
            return
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "scenario": self.sid, "case_id": case_id,
               "action": action_str[:1000], "seconds": round(seconds, 2), **asdict(decision)}
        with _LOG_LOCK, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
