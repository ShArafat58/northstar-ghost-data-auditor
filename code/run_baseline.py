"""Run a complete unlabelled sample baseline, with no Groq calls or label leakage."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from baseline import plan_case
from identity import IdentityResolver
from loaders import load_dataset
from observations import build_index
from validator import serialize_steps, validate_prediction


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("latest", "replica-safe", "intent-v2", "readback-v2", "verification-v3", "embedded-sql-v4", "media-evidence-v5", "structured-event-v6"), default="latest")
    parser.add_argument("--sample", action="store_true", help="Use 32 sample requests")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path)
    args = parser.parse_args()
    if not args.sample:
        parser.error("Step 8 is sample-only. The 518-case production runner requires a later approval.")
    root = Path(__file__).resolve().parent.parent
    dataset = load_dataset(root, sample=True)
    identity_resolver = IdentityResolver(dataset.identities, dataset.changes)
    index = build_index(dataset.evidence, enhanced_readback=args.variant in {"readback-v2", "verification-v3", "embedded-sql-v4", "media-evidence-v5", "structured-event-v6"},
                        enriched_media=args.variant == "verification-v3",
                        decode_sql_json=args.variant in {"embedded-sql-v4", "media-evidence-v5", "structured-event-v6"},
                        structured_attachment=args.variant in {"media-evidence-v5", "structured-event-v6"},
                        structured_event_v6=args.variant == "structured-event-v6")
    ids = frozenset(x.file_id for x in dataset.evidence)
    statuses = Counter()
    diagnostics = []
    rows = []
    for request in sorted(dataset.requests, key=lambda x: x.case_id):
        resolved = identity_resolver.resolve(request.email, request.audit_at)
        plan = plan_case(request, resolved, index, dataset.rules, variant=args.variant)
        validate_prediction(plan.status, plan.steps, evidence_ids=ids)
        statuses[plan.status] += 1
        rows.append((plan.case_id, plan.status, serialize_steps(plan.steps)))
        diagnostics.append({"case_id": plan.case_id, "status": plan.status,
                            "resources_seen": plan.resources_seen, "steps": len(plan.steps),
                            "warnings": list(plan.warnings)})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("case_id", "deletion_status", "steps_json"))
        writer.writerows(rows)
    diagnostic_path = args.diagnostics or args.out.with_suffix(".diagnostics.json")
    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_path.write_text(json.dumps({"variant": args.variant,
                                           "unreadable_evidence": list(index.unreadable_files),
                                           "media_with_no_structured_records": list(index.unparsed_media),
                                           "examined_files": len(index.examined_files),
                                           "observations": len(index.observations),
                                           "cases": diagnostics}, indent=2), encoding="utf-8")
    print(f"SAMPLE COVERAGE: {len(rows)}/32")
    print(f"EVIDENCE FILES EXAMINED: {len(index.examined_files)}/{len(dataset.evidence)}")
    print(f"RESOURCE OBSERVATIONS: {len(index.observations)}")
    print(f"UNREADABLE MEDIA: {len(index.unreadable_files)} (see {diagnostic_path})")
    print(f"MEDIA WITH NO STRUCTURED RECORDS: {len(index.unparsed_media)}")
    print(f"STATUS COUNTS: {dict(statuses)}")
    print(f"SAVED: {args.out}")
    print(f"DIAGNOSTICS: {diagnostic_path}")
    if index.unreadable_files or index.unparsed_media:
        print("WARNING: Media evidence remains unverified; this is an exploratory baseline, NOT submission-ready.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
