"""Run the frozen offline sharp-reversal exploratory experiment."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.research.sharp_reversal_experiment import DEFAULT_ROOT, write_experiment
from app.research.execution_integrity import TimestampSemanticsEvidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--spec", type=Path, default=Path("experiments/sharp_reversal_exploratory_v1.json"))
    parser.add_argument("--output", type=Path, default=Path("backtests/sharp_reversal_exploratory_v1"))
    parser.add_argument("--timestamp-evidence", type=Path,
                        help="reviewed evidence JSON binding verified START labels to exact data hashes")
    args = parser.parse_args()
    root = args.root.resolve()
    spec = args.spec if args.spec.is_absolute() else root / args.spec
    output = args.output if args.output.is_absolute() else root / args.output
    evidence_path = args.timestamp_evidence
    if evidence_path is not None and not evidence_path.is_absolute():
        evidence_path = root / evidence_path
    evidence = TimestampSemanticsEvidence.from_file(evidence_path) if evidence_path else None
    write_experiment(root, spec, output, timestamp_evidence=evidence)
    print(f"Exploratory results written to {output}")


if __name__ == "__main__":
    main()
