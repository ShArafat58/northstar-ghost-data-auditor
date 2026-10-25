"""Independent, strict validator for participant-facing root output.csv."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from loaders import load_dataset
from validator import validate_prediction


def validate_file(path: Path, repo_root: Path, *, sample: bool = False) -> dict[str, int]:
    data = load_dataset(repo_root, sample=sample)
    expected = sorted(r.case_id for r in data.requests)
    evidence_ids = frozenset(file.file_id for file in data.evidence)
    activity_ids = frozenset(row['activity_id'] for row in data.activities)
    audit_ids = frozenset(row.audit_id for row in data.changes)
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ['case_id', 'deletion_status', 'steps_json']:
            raise ValueError('Output header must be exactly case_id,deletion_status,steps_json')
        found: list[str] = []
        step_count = 0
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError('Malformed output CSV row')
            found.append(row['case_id'])
            steps = json.loads(row['steps_json'])
            if not isinstance(steps, list):
                raise ValueError('steps_json must be a JSON array')
            validate_prediction(row['deletion_status'], steps, evidence_ids=evidence_ids,
                                activity_ids=activity_ids, audit_ids=audit_ids)
            step_count += len(steps)
    if found != expected:
        raise ValueError(f'Wrong case IDs, count or ordering: found={len(found)}, expected={len(expected)}')
    return {'rows': len(found), 'steps': step_count, 'evidence_files': len(evidence_ids)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, help='CSV to check (default root output.csv)')
    parser.add_argument('--sample', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.out or root / 'output.csv'
    result = validate_file(output, root, sample=args.sample)
    print(f'VALID OUTPUT | rows={result["rows"]} | steps={result["steps"]} | evidence ids={result["evidence_files"]}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print('INVALID OUTPUT: ' + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
