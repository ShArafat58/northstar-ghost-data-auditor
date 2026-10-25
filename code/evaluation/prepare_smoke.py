"""Capture one REAL Step 6 smoke prediction without hand-editing expected labels.

A partial CSV is deliberate: no guessed rows, no invented success metrics.
"""

from __future__ import annotations

import argparse
import csv
import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COLUMNS = ("case_id", "deletion_status", "steps_json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True, help="Existing sample case to test")
    parser.add_argument("--evidence-id", required=True, help="Single participant-visible evidence file ID")
    parser.add_argument("--output", type=Path, default=ROOT / "code" / "evaluation" / "smoke_predictions.csv")
    args = parser.parse_args()
    execution = subprocess.run(
        [sys.executable, str(ROOT / "code" / "main.py"), "--sample", "--case-id", args.case_id, "--evidence-id", args.evidence_id],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if execution.returncode:
        raise RuntimeError(f"Step 6 smoke prediction failed:\n{execution.stderr}")
    lines = list(csv.DictReader(io.StringIO(execution.stdout)))
    if len(lines) != 1 or tuple(lines[0]) != COLUMNS or lines[0]["case_id"] != args.case_id:
        raise ValueError("Unexpected Step 6 smoke output; refused to save")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow(lines[0])
    print(f"SMOKE PREDICTION SAVED: {args.output}")
    print("ONE CASE ONLY: this is a partial diagnostic, not the 32-case baseline.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"SMOKE PREPARATION ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
