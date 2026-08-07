# Baseline Attack Results

Baseline attack success rate (ASR) for 12 locally-hosted models available through UF's NaviGator Toolkit, tested against the full FinVault attack suite (31 scenarios, 107 attack cases) with no hardening or defense layer applied. These numbers are the reference point that hardening scripts will be measured against.

## Setup

- **Benchmark:** [aifinlab/FinVault](https://github.com/aifinlab/FinVault)
- **Model access:** UF NaviGator Toolkit (`https://api.ai.it.ufl.edu/v1`), local/HiPerGator-hosted models only
- **Test mode:** `base` (no safety hardening applied)
- **Dataset:** `original`
- **Command:**
  ```bash
  python3.12 run_attack_test.py --all --agent <agent_name> --concurrency 8 --output results/baseline/cody_<agent_name>_baseline.json
  ```

`uf_nemotron_nano_8b` (`llama-3.1-nemotron-nano-8B-v1`) is listed as accessible by UF's `/v1/models` and team access-control endpoints, but is rejected as an "invalid model name" by `/chat/completions` — this appears to be a bug on UF's side and has been excluded from results pending a fix.

## Overall ASR by model

| Model | ASR | Successful Attacks |
|---|---|---|
| **gpt-oss-20b** | **14.9%** | 16 / 107 |
| **gpt-oss-120b** | **19.6%** | 21 / 107 |
| gemma-4-31b | 23.4% | 25 / 107 |
| mistral-small-3.1 | 34.6% | 37 / 107 |
| nemotron-nano-30b | 43.9% | 47 / 107 |
| llama-3.1-8b | 48.6% | 52 / 107 |
| mistral-7b | 49.5% | 53 / 107 |
| llama-3.1-70b | 51.4% | 55 / 107 |
| medgemma-27b | 51.4% | 55 / 107 |
| codestral-22b | 53.3% | 57 / 107 |
| granite-3.3-8b | 57.0% | 61 / 107 |
| **llama-3.3-70b** | **58.9%** | 63 / 107 |

## ASR by category

| Category | gpt-oss-20b | gpt-oss-120b | gemma-4-31b | mistral-small-3.1 | nemotron-nano-30b | llama-3.1-8b | mistral-7b | llama-3.1-70b | medgemma-27b | codestral-22b | granite-3.3-8b | llama-3.3-70b |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Compliance & AML | 8% | 29% | 25% | 42% | 29% | 46% | 58% | 54% | 54% | 54% | 54% | 58% |
| Credit & Lending | 13% | 22% | 22% | 30% | 57% | 39% | 35% | 48% | 43% | 57% | 43% | 48% |
| Insurance | 14% | 7% | 7% | 29% | 21% | 79% | 79% | 57% | 57% | 71% | 64% | 71% |
| Payment & Settlement | 29% | 21% | 29% | 29% | 43% | 21% | 36% | 29% | 64% | 21% | 64% | 50% |
| Risk Management | 13% | 7% | 27% | 33% | 53% | 53% | 47% | 53% | 33% | 53% | 60% | 60% |
| Securities & Investment | 18% | 24% | 29% | 41% | 59% | 59% | 47% | 65% | 59% | 59% | 65% | 71% |

## Key findings

- **Model family and training approach matter more than raw parameter count.** `gpt-oss-20b` (smaller) slightly outperforms `gpt-oss-120b`, and `llama-3.1-8b` beats `llama-3.3-70b` despite being a smaller, older model.
- **Insurance and Securities & Investment are the most consistently vulnerable categories** across nearly every model tested, while Compliance & AML is the most consistently well-defended, especially for the GPT-OSS family.
- **Recurring successful attack patterns** across models: authority impersonation, emotional urgency/manipulation, information extraction and filtering, and fabricated supporting evidence (fake documents, forged confirmations).
- `llama-3.3-70b` is the weakest baseline overall and is the primary target for hardening work going forward — it has the most room to demonstrate measurable improvement.

## Next steps

- Design and apply a hardening layer targeting the recurring attack patterns above (authority-claim detection, urgency framing, unverified-evidence acceptance)
- Re-run the full attack suite with hardening applied and compare against this baseline
- Investigate the `uf_nemotron_nano_8b` model-access bug with UF IT