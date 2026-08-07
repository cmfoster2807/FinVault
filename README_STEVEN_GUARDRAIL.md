# FinVault Input Guardrail Research

## Goal

Implement and evaluate an input-level guardrail for FinVault.

The guardrail will inspect incoming prompts before they are passed to the target LLM agent.

Planned flow:

```text
Incoming Prompt
      |
      v
Input Guardrail
      |
      +---- Attack detected ---> Block
      |
      +---- Safe -------------> FinVault Agent
```

The initial defense being investigated is **Llama Guard 3**.

## My Work

This work is being developed on the `steven-hardening` branch.

My focus is **input-level guardrail defenses** rather than modifying the target agent's system prompt or internal behavior.

## Initial Repository Findings

FinVault already contains defense-related code under:

```text
sandbox/defense/
```

Important files discovered:

```text
sandbox/defense/defense_manager.py
sandbox/defense/llama_guard/detector.py
sandbox/attack_testing/llm_agent.py
```

### Existing Llama Guard Code

`detector.py` contains a `LlamaGuard3Detector` implementation designed to classify input as safe or unsafe.

The detector returns information including:

* whether an attack was detected
* confidence
* detected categories
* category names
* execution time
* errors
* token usage

### Current Defense Manager

`defense_manager.py` contains a `DefenseManager`, but its current `detect()` implementation does not appear to actually invoke the Llama Guard detector.

### Agent Entry Point

FinVault's LLM agent is implemented in:

```text
sandbox/attack_testing/llm_agent.py
```

The relevant function is:

```python
generate_response(self, prompt: str)
```

Inside that function, the prompt is formatted and eventually passed to the target model through:

```python
response = self.client.invoke(messages)
```

This appears to be the appropriate location for an input guardrail check before the target model receives the prompt.

## Baseline

Existing scenario 00 baseline results were found under:

```text
sandbox/results/
```

including:

```text
navigator_baseline_s00_smoke.json
navigator_baseline_s00_full.json
```

These may be used later to compare baseline attack success rate against the guarded implementation.

## Planned Work

1. Connect the existing Llama Guard detector to the FinVault agent pipeline.
2. Verify the integration on a small test.
3. Run a small subset of scenario 00.
4. Compare guarded results with Cody's baseline.
5. Measure attack blocking and false positives.
6. Investigate attacks that bypass the guardrail.
7. Improve the guardrail configuration or strategy.
8. Expand evaluation to additional models/scenarios.

## Change Log
- Began Llama Guard integration by importing `LlamaGuard3Detector` into `sandbox/attack_testing/llm_agent.py`.
- Initialized `LlamaGuard3Detector` inside `LLMAgent.__init__()` using mock mode for initial integration testing.
- Added an input guardrail check inside `LLMAgent.generate_response()` before `self.client.invoke(messages)`. Inputs classified as attacks are blocked and redirected to `escalate_to_human`.
- Verified the initial input-guardrail integration in mock mode: a test prompt containing instruction-override/bypass language was classified as an attack and blocked before reaching the target LLM.
- Completed the initial Llama Guard integration in mock mode. Attack-like inputs are blocked before reaching the target LLM, while benign inputs are allowed through normally.

### August 7, 2026

* Updated local repository with the latest project changes.
* Received Abhay's hardening script as a reference example.
* Confirmed that Abhay's script is not intended to be executed directly.
* Selected input-level guardrails as my defense direction.
* Located FinVault's existing Llama Guard implementation.
* Located the FinVault LLM agent execution path.
* Identified the likely input-guardrail insertion point.
* No guardrail code changes have been made yet.

## Next Step

Create the smallest possible Llama Guard integration and verify that prompts can be inspected before reaching the target FinVault agent.
