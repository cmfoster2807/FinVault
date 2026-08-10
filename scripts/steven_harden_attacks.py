#!/usr/bin/env python3

import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

SOURCE_DIR = ROOT / "sandbox" / "attack_datasets"
OUTPUT_DIR = ROOT / "sandbox" / "attack_datasets_steven" / "v0_control"


TARGETS = {
    "02": "ATTACK_V1_002_elderly_surgery",
    "03": "ATTACK_V2_001_risk_override",
    "29": "ATTACK_V2_003_skip_trace",
}


def prompt_hash(prompt: str) -> str:
    """Short hash used to verify the attack prompt was not modified."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


def load_scenario(scenario_id: str) -> dict:
    path = SOURCE_DIR / f"scenario_{scenario_id}_attacks.json"

    if not path.exists():
        raise FileNotFoundError(f"Missing source dataset: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_attack(scenario: dict, attack_id: str) -> dict:
    for attack in scenario.get("attacks", []):
        current_id = attack.get("id") or attack.get("case_id")

        if current_id == attack_id:
            return attack

    raise ValueError(
        f"Could not find attack {attack_id} "
        f"in scenario {scenario.get('scenario_id')}"
    )


def build_control_dataset(scenario_id: str, attack_id: str) -> dict:
    original_scenario = load_scenario(scenario_id)
    original_attack = find_attack(original_scenario, attack_id)

    # Preserve the original FinVault structure.
    output_scenario = copy.deepcopy(original_scenario)

    # Version 0 contains only our selected experimental case.
    output_scenario["attacks"] = [copy.deepcopy(original_attack)]

    return output_scenario


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest = {
        "experiment": "steven_v0_control",
        "description": (
            "Control dataset containing selected original FinVault attacks "
            "with attack prompts unchanged."
        ),
        "cases": [],
    }

    print("Steven FinVault hardening experiment")
    print("Version: v0_control")
    print()

    for scenario_id, attack_id in TARGETS.items():
        dataset = build_control_dataset(scenario_id, attack_id)

        attack = dataset["attacks"][0]
        prompt = attack["attack_prompt"]

        output_path = OUTPUT_DIR / f"scenario_{scenario_id}_attacks.json"

        with output_path.open("w", encoding="utf-8") as f:
            json.dump(dataset, f, ensure_ascii=False, indent=2)

        manifest["cases"].append(
            {
                "scenario_id": scenario_id,
                "attack_id": attack_id,
                "attack_type": attack.get("attack_type"),
                "vulnerability": (
                    attack.get("target_vulnerability")
                    or attack.get("vulnerability")
                ),
                "prompt_sha256": prompt_hash(prompt),
                "prompt_modified": False,
            }
        )

        print(
            f"[OK] scenario {scenario_id}: {attack_id}\n"
            f"     type: {attack.get('attack_type')}\n"
            f"     prompt hash: {prompt_hash(prompt)}\n"
            f"     output: {output_path}"
        )

    manifest_path = OUTPUT_DIR / "manifest.json"

    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print()
    print(f"Manifest written to: {manifest_path}")
    print("Control dataset generation complete.")


if __name__ == "__main__":
    main()