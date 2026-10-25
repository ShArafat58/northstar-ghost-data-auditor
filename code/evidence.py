"""Auditable, cutoff-aware retrieval from participant-visible evidence files.

This module discovers *candidate facts*. It does not treat an activity result,
log message, or arbitrary text hit as proof of a completed deletion.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from identity import ResolvedIdentity
from loaders import EvidenceFile

RESOURCE_PATTERN = re.compile(
    r"\b(?:account|search|support|analytics|export|backup|data_flow|"
    r"audit_record|cache|marketing|email_delivery|telemetry)_[A-Za-z0-9_-]+\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class EvidenceHit:
    file_id: str
    system_id: str
    resource_id: str
    category: str | None
    state: str | None
    source: str
    captured_at: datetime
    detail: str
    verified_record: bool


@dataclass(frozen=True, slots=True)
class EvidenceScan:
    hits: tuple[EvidenceHit, ...]
    scanned_files: tuple[str, ...]
    unsupported_files: tuple[str, ...]


TABLE_CLASSES: tuple[tuple[str, str, str], ...] = (
    ("customer_profiles", "account_service", "USER_PROFILE"),
    ("user_profiles", "account_service", "USER_PROFILE"),
    ("account_tombstone", "account_service", "ACCOUNT_TOMBSTONE"),
    ("search", "search_service", "SEARCH_INDEX_ENTRY"),
    ("support_attachment", "support_platform", "SUPPORT_ATTACHMENT"),
    ("support", "support_platform", "SUPPORT_MESSAGE"),
    ("analytics", "analytics_platform", "ANALYTICS_PROFILE"),
    ("telemetry", "analytics_platform", "PRODUCT_TELEMETRY"),
    ("backup", "backup_service", "BACKUP_ARCHIVE"),
    ("export", "export_service", "USER_EXPORT"),
    ("marketing", "marketing_service", "MARKETING_PROFILE"),
    ("cache", "cache_service", "CACHE_ENTRY"),
    ("audit", "security_service", "SECURITY_AUDIT_LOG"),
    ("email_delivery", "email_delivery_service", "EMAIL_DELIVERY_LOG"),
    ("connector", "integration_service", "DATA_FLOW"),
)


def table_class(table: str) -> tuple[str | None, str | None]:
    """Classify only recognizable table names, not guessed record meanings."""
    key = table.casefold()
    for prefix, system, category in TABLE_CLASSES:
        if prefix in key:
            return system, category
    return None, None


def _identity_values(identity: ResolvedIdentity) -> tuple[set[str], set[str]]:
    texts = {value.casefold() for value in (*identity.emails, *identity.phones)}
    hashes = {value.casefold() for value in identity.email_hashes}
    return texts, hashes


def _record_matches(row: dict[str, Any], identity: ResolvedIdentity) -> bool:
    """Match typed identifier columns exactly; never use partial user IDs."""
    texts, hashes = _identity_values(identity)
    for field, raw in row.items():
        if raw is None:
            continue
        value = str(raw).strip().casefold()
        key = field.casefold()
        if key in {"user_id", "customer_reference", "subject_reference", "subject_id"}:
            if value == identity.user_id.casefold():
                return True
        elif key in {"email", "customer_email", "requester_email", "lookup_key"}:
            if value in texts:
                return True
        elif key in {"phone", "customer_phone", "requester_phone"}:
            if value in texts:
                return True
        elif key in {"email_hash", "customer_email_hash"}:
            if value in hashes:
                return True
    return False


def _text_mentions_identity(text: str, identity: ResolvedIdentity) -> bool:
    """Require token boundaries to prevent user_05 matching user_050."""
    candidates = {identity.user_id, *identity.emails, *identity.phones, *identity.email_hashes}
    for value in candidates:
        if value and re.search(r"(?<![\w@.+-])" + re.escape(value) + r"(?![\w@.+-])", text, re.I):
            return True
    return False


def scan_sql(file: EvidenceFile, identity: ResolvedIdentity) -> list[EvidenceHit]:
    """Execute a SQLite-compatible *snapshot* in memory and query its rows.

    Rows in any relevant replica are real surviving copies; no successful
    deletion is inferred from an unrelated table's missing row.
    """
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(file.path.read_text(encoding="utf-8-sig"))
        names = [item[0] for item in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        hits: list[EvidenceHit] = []
        for table in names:
            system, category = table_class(table)
            if not system:
                continue
            safe_table = '"' + table.replace('"', '""') + '"'
            for record in connection.execute(f"SELECT * FROM {safe_table}"):
                row = dict(record)
                if not _record_matches(row, identity):
                    continue
                resource_id = str(row.get("resource_id") or row.get("record_id") or "")
                if not resource_id:
                    continue
                hits.append(EvidenceHit(
                    file_id=file.file_id,
                    system_id=system,
                    resource_id=resource_id,
                    category=category,
                    state="PRESENT",
                    source="sql",
                    captured_at=file.captured_at,
                    detail=f"table={table}; resource exists in snapshot",
                    verified_record=True,
                ))
        return hits
    finally:
        connection.close()


def _json_records(root: Any) -> Iterable[dict[str, Any]]:
    if isinstance(root, list):
        for item in root:
            yield from _json_records(item)
    elif isinstance(root, dict):
        if "resource_id" in root:
            yield root
        for value in root.values():
            if isinstance(value, (dict, list)):
                yield from _json_records(value)


def scan_json(file: EvidenceFile, identity: ResolvedIdentity) -> list[EvidenceHit]:
    """Return candidate JSON records without assuming their policy category."""
    root = json.loads(file.path.read_text(encoding="utf-8-sig"))
    hits: list[EvidenceHit] = []
    for row in _json_records(root):
        if not _record_matches(row, identity):
            continue
        resource_id = str(row["resource_id"])
        system = str(row.get("system_id") or row.get("system") or "unknown")
        category = str(row.get("data_category") or "") or None
        hits.append(EvidenceHit(
            file_id=file.file_id,
            system_id=system,
            resource_id=resource_id,
            category=category,
            state=None,
            source="json",
            captured_at=file.captured_at,
            detail="JSON record contains an exact identity field match; state requires review",
            verified_record=False,
        ))
    return hits


def scan_text(file: EvidenceFile, identity: ResolvedIdentity) -> list[EvidenceHit]:
    """Find provenance-preserving log/text candidates, not deletion verdicts."""
    hits: list[EvidenceHit] = []
    with file.path.open("r", encoding="utf-8-sig", errors="replace") as stream:
        for line_no, line in enumerate(stream, start=1):
            if not _text_mentions_identity(line, identity):
                continue
            resources = set(RESOURCE_PATTERN.findall(line))
            for resource in resources:
                hits.append(EvidenceHit(
                    file_id=file.file_id,
                    system_id="unknown",
                    resource_id=resource,
                    category=None,
                    state=None,
                    source="text",
                    captured_at=file.captured_at,
                    detail=f"line={line_no}; identity-linked resource requires review",
                    verified_record=False,
                ))
    return hits


def retrieve_evidence(
    files: tuple[EvidenceFile, ...],
    identity: ResolvedIdentity,
    audit_at: datetime,
    *,
    restrict_ids: frozenset[str] | None = None,
) -> EvidenceScan:
    """Discard future snapshots; explicitly report formats not yet inspected."""
    available_ids = {file.file_id for file in files}
    if restrict_ids is not None and not restrict_ids <= available_ids:
        raise ValueError(f"Unknown evidence IDs: {sorted(restrict_ids - available_ids)}")
    hits: list[EvidenceHit] = []
    scanned: list[str] = []
    unsupported: list[str] = []
    for file in files:
        if file.captured_at > audit_at:
            continue
        if restrict_ids is not None and file.file_id not in restrict_ids:
            continue
        suffix = file.path.suffix.lower()
        if suffix == ".sql":
            hits.extend(scan_sql(file, identity))
        elif suffix == ".json":
            hits.extend(scan_json(file, identity))
        elif suffix in {".txt", ".log"}:
            hits.extend(scan_text(file, identity))
        else:
            unsupported.append(file.file_id)
            continue
        scanned.append(file.file_id)
    hits.sort(key=lambda hit: (hit.file_id, hit.system_id, hit.resource_id, hit.source))
    return EvidenceScan(tuple(hits), tuple(scanned), tuple(unsupported))
