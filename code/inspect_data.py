"""Step 5 smoke test: validate data loading and historical identity coverage."""

from __future__ import annotations

from pathlib import Path

from identity import IdentityResolver
from loaders import load_dataset


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    dataset = load_dataset(repo_root)
    resolver = IdentityResolver(dataset.identities, dataset.changes)

    matched_snapshot = 0
    matched_historical = 0
    unmatched: list[str] = []
    for request in dataset.requests:
        identity = resolver.resolve(request.email, request.audit_at)
        if identity is None:
            unmatched.append(request.case_id)
        elif identity.matched_via == "snapshot":
            matched_snapshot += 1
        else:
            matched_historical += 1

    print(f"Requests: {len(dataset.requests)}")
    print(f"User profiles: {len(dataset.identities)}")
    print(f"Identity changes: {len(dataset.changes)}")
    print(f"System activities: {len(dataset.activities)}")
    print(f"Deletion rules: {len(dataset.rules)}")
    print(f"Evidence files: {len(dataset.evidence)}")
    print(f"Matched via snapshot: {matched_snapshot}")
    print(f"Matched via historical records: {matched_historical}")
    print(f"Unmatched cases: {len(unmatched)}")
    if unmatched:
        print("Unmatched case IDs:", ", ".join(unmatched))

    if len(dataset.requests) != 518:
        raise SystemExit("Unexpected request count")
    if unmatched:
        raise SystemExit("Identity coverage incomplete")
    print("STEP 5 DATA AND IDENTITY CHECK: PASS")


if __name__ == "__main__":
    main()
