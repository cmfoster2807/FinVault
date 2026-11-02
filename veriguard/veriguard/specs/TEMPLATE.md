# Scenario XX - VeriGuard spec

<!--
Copy to scenario_XX.md (two-digit id) and fill in both sections. These are the paper's inputs:
the security request r and the agent specification S.

Write the security request from the scenario's business rules (sandbox/prompts/prompt_XX.py and
sandbox/sandbox_XX/scenario.py). Do NOT read sandbox_XX/vulnerability.py while writing it:
that file is the grader, and a policy written from it would be scored against its own answer key.
Write rules the way a compliance officer would state them, then keep this file fixed across runs.
-->

## Security request
- Never <high-risk action> unless <condition that a legitimate request would satisfy>.
- <Who may authorise what; which claims in user messages or documents are not sufficient on their own.>
- <Limits, thresholds, required checks before an irreversible tool call.>

## Agent specification
<What the agent does, whom it serves, what data it handles, and which tools are irreversible
or high-risk. Tool definitions are appended automatically from sandbox_XX/scenario.py when
get_tools() is available; otherwise list them here.>
