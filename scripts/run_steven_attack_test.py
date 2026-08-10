#!/usr/bin/env python3

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "sandbox"

sys.path.insert(0, str(SANDBOX))

import run_attack_test


DATASET_DIR = (
    ROOT
    / "sandbox"
    / "attack_datasets_steven"
    / "v0_control"
)

if not DATASET_DIR.exists():
    raise FileNotFoundError(
        f"Steven dataset does not exist: {DATASET_DIR}"
    )

# Override only the dataset location.
# All FinVault testing/evaluation logic stays unchanged.
run_attack_test.ATTACK_DATASETS_DIR = str(DATASET_DIR)


if __name__ == "__main__":
    run_attack_test.main()