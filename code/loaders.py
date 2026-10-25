"""Strict, reusable loaders for the participant-facing deletion audit dataset."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SCHEMAS: dict[str, tuple[str, ...]] = {
    "deletion_requests.csv": (
        "case_id", "email", "requested_at", "reported_status", "reported_at",
        "audit_at", "request_text",
    ),
    "sample_deletion_requests.csv": (
        "case_id", "email", "requested_at", "reported_status", "reported_at",
        "audit_at", "request_text",
    ),
    "user_identities.csv": (
        "user_id", "name", "email", "phone", "email_hash", "updated_at",
    ),
    "user_audit_log.csv": (
        "audit_id", "user_id", "event_time", "field_name", "old_value", "new_value",
    ),
    "system_activity.csv": (
        "activity_id", "event_time", "event_type", "source_system",
        "target_system", "resource_id", "reported_result", "details",
    ),
    "deletion_rules.csv": (
        "rule_id", "data_category", "condition", "required_action",
    ),
    "evidence_files.csv": (
        "file_id", "file_path", "origin", "captured_at",
    ),
}


@dataclass(frozen=True, slots=True)
class DeletionRequest:
    case_id: str
    email: str
    requested_at: datetime
    reported_status: str
    reported_at: datetime
    audit_at: datetime
    request_text: str


@dataclass(frozen=True, slots=True)
class UserIdentity:
    user_id: str
    name: str
    email: str
    phone: str
    email_hash: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IdentityChange:
    audit_id: str
    user_id: str
    event_time: datetime
    field_name: str
    old_value: str
    new_value: str


@dataclass(frozen=True, slots=True)
class EvidenceFile:
    file_id: str
    path: Path
    origin: str
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class Dataset:
    requests: tuple[DeletionRequest, ...]
    identities: tuple[UserIdentity, ...]
    changes: tuple[IdentityChange, ...]
    activities: tuple[dict[str, str], ...]
    rules: tuple[dict[str, str], ...]
    evidence: tuple[EvidenceFile, ...]


def parse_timestamp(value: str, *, context: str) -> datetime:
    """Reject missing or timezone-naive timestamps to prevent cutoff mistakes."""
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp at {context}: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Timestamp missing timezone at {context}")
    return parsed


def read_table(dataset_dir: Path, filename: str) -> tuple[dict[str, str], ...]:
    """Read one known CSV, rejecting schema drift and malformed records."""
    if filename not in SCHEMAS:
        raise ValueError(f"Unrecognized dataset table: {filename}")
    path = dataset_dir / filename
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or tuple(reader.fieldnames) != SCHEMAS[filename]:
            raise ValueError(f"Unexpected columns in {filename}: {reader.fieldnames}")
        records: list[dict[str, str]] = []
        for line_no, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV record in {filename}, line {line_no}")
            records.append(row)
    return tuple(records)


def ensure_unique(rows: tuple[dict[str, str], ...], key: str, filename: str) -> None:
    values = [row[key] for row in rows]
    if any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError(f"Empty or duplicate {key} in {filename}")


def load_dataset(repo_root: Path, *, sample: bool = False) -> Dataset:
    """Load input and evidence metadata; never read labelled sample answers."""
    dataset_dir = repo_root.resolve() / "dataset"
    request_file = "sample_deletion_requests.csv" if sample else "deletion_requests.csv"
    request_rows = read_table(dataset_dir, request_file)
    identity_rows = read_table(dataset_dir, "user_identities.csv")
    change_rows = read_table(dataset_dir, "user_audit_log.csv")
    activity_rows = read_table(dataset_dir, "system_activity.csv")
    rule_rows = read_table(dataset_dir, "deletion_rules.csv")
    evidence_rows = read_table(dataset_dir, "evidence_files.csv")

    for rows, key, filename in (
        (request_rows, "case_id", request_file),
        (identity_rows, "user_id", "user_identities.csv"),
        (change_rows, "audit_id", "user_audit_log.csv"),
        (activity_rows, "activity_id", "system_activity.csv"),
        (rule_rows, "rule_id", "deletion_rules.csv"),
        (evidence_rows, "file_id", "evidence_files.csv"),
    ):
        ensure_unique(rows, key, filename)

    requests = tuple(
        DeletionRequest(
            case_id=row["case_id"],
            email=row["email"],
            requested_at=parse_timestamp(row["requested_at"], context=row["case_id"]),
            reported_status=row["reported_status"],
            reported_at=parse_timestamp(row["reported_at"], context=row["case_id"]),
            audit_at=parse_timestamp(row["audit_at"], context=row["case_id"]),
            request_text=row["request_text"],
        )
        for row in request_rows
    )
    identities = tuple(
        UserIdentity(
            user_id=row["user_id"],
            name=row["name"],
            email=row["email"],
            phone=row["phone"],
            email_hash=row["email_hash"],
            updated_at=parse_timestamp(row["updated_at"], context=row["user_id"]),
        )
        for row in identity_rows
    )
    changes = tuple(
        IdentityChange(
            audit_id=row["audit_id"],
            user_id=row["user_id"],
            event_time=parse_timestamp(row["event_time"], context=row["audit_id"]),
            field_name=row["field_name"],
            old_value=row["old_value"],
            new_value=row["new_value"],
        )
        for row in change_rows
    )
    evidence: list[EvidenceFile] = []
    for row in evidence_rows:
        path = (dataset_dir / row["file_path"]).resolve()
        if not path.is_relative_to(dataset_dir) or not path.is_file():
            raise ValueError(f"Invalid/missing evidence path for {row['file_id']}")
        evidence.append(
            EvidenceFile(
                file_id=row["file_id"],
                path=path,
                origin=row["origin"],
                captured_at=parse_timestamp(row["captured_at"], context=row["file_id"]),
            )
        )
    return Dataset(
        requests=requests,
        identities=identities,
        changes=changes,
        activities=activity_rows,
        rules=rule_rows,
        evidence=tuple(evidence),
    )
