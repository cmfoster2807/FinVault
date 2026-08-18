#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "sandbox"

sys.path.insert(0, str(SANDBOX))

import run_attack_test


DATASETS = {
    "control": ROOT
    / "sandbox"
    / "attack_datasets_steven"
    / "v0_control",

    "context": ROOT
    / "sandbox"
    / "attack_datasets_steven"
    / "v1"
    / "context",

    "decomposition": ROOT
    / "sandbox"
    / "attack_datasets_steven"
    / "v1"
    / "decomposition",

    "operational": ROOT
    / "sandbox"
    / "attack_datasets_steven"
    / "v1"
    / "operational",
}


def main():
    parser = argparse.ArgumentParser(add_help=False)

    parser.add_argument(
        "--steven-dataset",
        choices=DATASETS.keys(),
        default="control",
        help="Steven experimental dataset to use",
    )

    args, remaining_args = parser.parse_known_args()

    dataset_dir = DATASETS[args.steven_dataset]

    if not dataset_dir.exists():
        raise FileNotFoundError(
            f"Steven dataset does not exist: {dataset_dir}"
        )

    print(
        f"[Steven Experiment] Dataset: {args.steven_dataset}\n"
        f"[Steven Experiment] Path: {dataset_dir}\n"
    )

    # Override only FinVault's attack dataset location.
    run_attack_test.ATTACK_DATASETS_DIR = str(dataset_dir)

    # Remove our custom argument before passing control
    # to FinVault's original CLI.
    sys.argv = [sys.argv[0]] + remaining_args

    run_attack_test.main()


if __name__ == "__main__":
    main()