"""Run the Nagini static verifier on an annotated policy file."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

NAGINI = os.environ.get("NAGINI_BIN", "nagini")


def available() -> bool:
    return shutil.which(NAGINI) is not None or Path(NAGINI).exists()


def verify(code: str, timeout: int = 600) -> tuple[bool, str]:
    """Return (verified, output). Success is judged by exit code plus Nagini's success line."""
    if not available():
        return False, f"Nagini not found ({NAGINI}); install it or set NAGINI_BIN"
    with tempfile.TemporaryDirectory() as d:
        path = Path(d, "policy_verify.py")
        path.write_text(code, encoding="utf-8")
        try:
            r = subprocess.run([NAGINI, str(path)], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"Nagini timed out after {timeout}s"
    out = r.stdout + r.stderr
    ok = r.returncode == 0 and "Verification successful" in out
    return ok, out[-4000:]
