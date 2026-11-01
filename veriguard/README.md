# VeriGuard on FinVault

A reimplementation of VeriGuard (Miculicich et al., 2025, arXiv:2510.05156) as a defense
for FinVault. No official code exists; this follows the paper's method and Appendix A.

```
veriguard/
  specs/          r and S per scenario (you write these)
  policies/       generated policy per scenario (runtime reads these)
  runs/           full trace of every generation run (commit these)
  logs/           runtime allow/deny log, one JSONL per run
  generate.py     offline: generate -> validate -> test (-> verify)
  enforce.py      online: arg extraction + policy check before env.step()
  prompts.py      all prompt templates
  verify_nagini.py
```

## 1. Setup

```bash
pip install openai pytest          # in the same env FinVault uses
export VERIGUARD_API_KEY=...       # NaviGator key (falls back to OPENAI_API_KEY)
export VERIGUARD_MODEL=...         # exact model id, e.g. the one in agents_config.yaml
```

Put the `veriguard/` folder at the repo root, next to `sandbox/`.

## 2. Write a spec

```bash
cp veriguard/specs/TEMPLATE.md veriguard/specs/scenario_02.md
```

Fill in the security request from the scenario's business rules. Do not read
`vulnerability.py` while writing it: that is the grader.

## 3. Generate the policy

```bash
python -m veriguard.generate -s 02 --dry-run   # spec + tool definitions load? no LLM calls
python -m veriguard.generate -s 02             # writes policies/scenario_02.py + runs/ trace
```

Open the trace in `runs/` and read the final policy before using it. Check whether the generated
tests were sensible: the loop fixes the policy to pass them, so a wrong test can bend the policy.

## 4. Wire it into `sandbox/run_attack_test.py`

**Imports** (top of file):

```python
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root, so `veriguard` imports
from veriguard import enforce as veriguard_enforce
```

**Flags** (next to the other `add_argument` calls):

```python
parser.add_argument('--defense', choices=['none', 'veriguard'], default='none')
parser.add_argument('--vg-model', type=str, default=None, help='Model for VeriGuard arg extraction')
parser.add_argument('--vg-fail-mode', choices=['closed', 'open'], default='closed')
```

**After `args = parser.parse_args()`**:

```python
if args.defense == 'veriguard':
    veriguard_enforce.configure(enabled=True, model=args.vg_model, fail_mode=args.vg_fail_mode)
```

**In the interaction loop**, replace the single `env.step(action_str)` line with:

```python
vg = veriguard_enforce.get_veriguard(scenario_id)   # use this function's scenario-id variable
if vg is not None:
    vg_context = f"User turns:\n{context_with_history}\n\nLatest observation:\n{obs_str}"
    decision = vg.check(action_str, context=vg_context, case_id=case_id)
    if not decision.allowed:
        turn_interactions += 1
        turns.append({
            "multi_turn": turn_idx + 1, "interaction": interaction + 1,
            "action": action_str[:500], "reward": 0, "vulnerabilities": [],
            "blocked_by": "veriguard", "block_reason": decision.reason,
        })
        obs = vg.blocked_observation(decision)
        continue

new_obs, reward, terminated, truncated, step_info = env.step(action_str)
if vg is not None:
    vg.record(case_id, action_str, new_obs)
```

A blocked call never reaches the environment, so it cannot trigger a vulnerability, and the
scoring code is untouched. With `--defense none` (the default) nothing changes.

## 5. Run

```bash
python sandbox/run_attack_test.py -s 02 --defense veriguard --vg-model <id> [usual flags]
```

Compare against the same command with `--defense none` and with your hardened prompts.
Use the JSONL in `logs/` to count blocks per case, and check how many blocks hit benign
actions, since FinVault's ASR alone will not show utility loss.

## Choices this implementation makes that the paper leaves open

- Security request r is hand-written per scenario from business rules, never from the grader.
- One model runs every generation stage; `--max-iters` (N) defaults to 3.
- Validation runs in the paper's autonomous mode (no user in the loop).
- Enforcement is Tool Execution Halt + Collaborative Re-planning, the paper's best setting.
- The extractor sees the proposed call, recent executed calls and their results, the user
  turns, and the latest observation. `tool_name` is taken from the call itself, not extracted.
- On extraction or policy errors the call is denied (`--vg-fail-mode closed`).
- Missing arguments for high-risk approvals are denied by instruction in the generation prompt.
- The test-fix prompt is told not to weaken the policy to satisfy a test that contradicts r.
- Nagini is optional (`--verify`). The annotated version replaces the tested one only if
  it still passes the same tests. Confirm the success string in `verify_nagini.py`
  ("Verification successful") matches your Nagini version's output.

## Known risks

- Generated policy code is executed in-process. `util.check_policy_code` rejects obvious
  I/O and dynamic execution, but read each policy before running it.
- The argument extractor is an LLM reading untrusted content, so it is an attack surface of
  its own: the policy only judges what the extractor reports.
- If the runner's parallel mode uses spawned processes rather than forked ones, call
  `configure()` inside each worker too.
