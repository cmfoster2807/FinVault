"""Parsing helpers and a basic safety check for LLM-generated policy code."""
import json
import re

_BANNED = [
    r"\bimport\s+(os|sys|subprocess|socket|shutil|pathlib|requests|urllib|importlib|ctypes)\b",
    r"\bfrom\s+(os|sys|subprocess|socket|shutil|pathlib|requests|urllib|importlib|ctypes)\b",
    r"\bopen\s*\(", r"\beval\s*\(", r"\bexec\s*\(", r"__import__", r"\bcompile\s*\(",
]


def extract_code(text: str) -> str:
    """Return the longest fenced code block in an LLM reply."""
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", text, re.S)
    if not blocks:
        raise ValueError("no fenced code block in LLM output")
    return max(blocks, key=len).strip() + "\n"


def extract_json(text: str) -> dict:
    """Return the first parseable JSON object in an LLM reply."""
    candidates = re.findall(r"```(?:json)?[ \t]*\n(.*?)```", text, re.S) + [text]
    for c in candidates:
        start, end = c.find("{"), c.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(c[start:end + 1])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON object in LLM output")


def check_policy_code(code: str) -> None:
    """Reject policy code that does I/O or dynamic execution, and confirm the interface loads."""
    for pat in _BANNED:
        if re.search(pat, code):
            raise ValueError(f"policy code contains a disallowed construct: {pat}")
    ns: dict = {}
    exec(compile(code, "<policy>", "exec"), ns)  # code passed the static check above
    if not callable(ns.get("policy")):
        raise ValueError("policy() is not defined")
    spec = ns.get("list_of_arguments_to_read")
    if not isinstance(spec, dict) or not spec:
        raise ValueError("list_of_arguments_to_read is missing or empty")
    invented = [k for k in spec if re.fullmatch(r"(episode|session|request|conversation|workflow)_?id", k, re.I)]
    if invented:
        raise ValueError(f"list_of_arguments_to_read requests identifiers that no tool call or result "
                         f"contains: {invented}. Remove them; 'earlier in this episode' means 'in the "
                         f"tool history the extractor is given'.")