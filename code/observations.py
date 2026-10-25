"""Parse participant-visible evidence into time-bound, provenance-preserving observations.

No reference answers are imported. In-memory SQLite is used only for supplied SQL
snapshots. Unsupported media are reported explicitly instead of treated as absent.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from loaders import EvidenceFile, parse_timestamp

RESOURCE = re.compile(r"\b(?:account|tombstone|search|support|analytics|export|backup|data_flow|audit_record|cache|marketing|email_delivery|telemetry)_[A-Za-z0-9_-]+\b", re.I)
TOKEN = re.compile(r"(?<![\w@.+-])(?:[\w.+-]+@[\w.-]+|user_\d+|[0-9a-f]{64})(?![\w@.+-])", re.I)
TRACE = re.compile(r"\btrace_id=([^\s]+)")
TIMESTAMP = re.compile(r"^\s*(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)")
PAIRS = re.compile(r'(\w+)=("[^"]*"|\S+)')

SYSTEM_CATEGORIES = {
    "account": ("account_service", "USER_PROFILE"),
    "tombstone": ("identity_service", "ACCOUNT_TOMBSTONE"),
    "search": ("search_service", "SEARCH_INDEX_ENTRY"),
    "support": ("support_platform", "SUPPORT_MESSAGE"),
    "analytics": ("analytics_platform", "ANALYTICS_PROFILE"),
    "export": ("export_service", "USER_EXPORT"),
    "backup": ("backup_service", "BACKUP_ARCHIVE"),
    "data_flow": ("integration_service", "DATA_FLOW"),
    "audit_record": ("security_service", "SECURITY_AUDIT_LOG"),
    "cache": ("cache_service", "CACHE_ENTRY"),
    "marketing": ("marketing_service", "MARKETING_PROFILE"),
    "email_delivery": ("email_delivery_service", "EMAIL_DELIVERY_LOG"),
    "telemetry": ("analytics_platform", "PRODUCT_TELEMETRY"),
}

IDENTITY_KEYS = frozenset({
    "user_id", "customer_reference", "subject_reference", "subject_id",
    "email", "owner_email", "requester_email", "customer_email",
    "phone", "owner_phone", "requester_phone", "customer_phone", "email_hash",
    "customer_email_hash", "lookup_key", "subject_key", "match_key", "scan_key",
    "correlation_key", "matched_value", "subject_ref", "customer_ref",
})
# Explicit graph edges only. Generic text proximity must never establish ownership.
RESOURCE_LINK_KEYS = frozenset({
    "source_resource_id", "subject_resource_id", "parent_resource_id",
    "account_resource_id", "profile_resource_id", "linked_resource_id",
    "owner_resource_id", "original_resource_id", "referenced_resource_id",
})
PII_KEYS = frozenset({
    "email", "owner_email", "requester_email", "customer_email", "phone",
    "owner_phone", "requester_phone", "customer_phone", "email_hash",
    "customer_email_hash", "name", "full_name", "requester_name",
    "display_name",
})


@dataclass(frozen=True, slots=True)
class Observation:
    file_id: str
    observed_at: datetime
    resource_id: str
    system_id: str
    category: str
    state: str
    identifiers: tuple[str, ...]
    detail: str
    is_direct_proof: bool = True
    linked_resources: tuple[str, ...] = ()


def resource_class(resource_id: str) -> tuple[str, str]:
    prefix = resource_id.rsplit("_", 1)[0].lower()
    if prefix in SYSTEM_CATEGORIES:
        return SYSTEM_CATEGORIES[prefix]
    return ("unknown", "UNKNOWN")


def table_class(table: str, resource_id: str) -> tuple[str, str]:
    system, category = resource_class(resource_id)
    name = table.lower()
    if "attachment" in name:
        return "support_platform", "SUPPORT_ATTACHMENT"
    if "tombstone" in name or "deleted_customer_reference" in name:
        return "identity_service", "ACCOUNT_TOMBSTONE"
    if "telemetry" in name:
        return "analytics_platform", "PRODUCT_TELEMETRY"
    if "secondary" in name or "identifier" in name:
        return "identity_service", "SECONDARY_IDENTIFIER"
    if "legal_hold" in name:
        return "legal_service", "LEGAL_HOLD_RECORD"
    if "email_delivery" in name:
        return "email_delivery_service", "EMAIL_DELIVERY_LOG"
    return system, category


def _flag(row: dict[str, Any], field: str) -> bool | None:
    value = row.get(field)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in {"1", "true", "yes", "enabled", "active"}:
        return True
    if str(value).strip().lower() in {"0", "false", "no", "disabled", "inactive"}:
        return False
    return None


def state_from_record(row: dict[str, Any], category: str) -> str | None:
    """Recognize readback states, not unverified workflow success claims."""
    if category == "DATA_FLOW":
        enabled = _flag(row, "enabled")
        if enabled is not None:
            return "ACTIVE" if enabled else "DISABLED"
    if category == "BACKUP_ARCHIVE":
        allowed = _flag(row, "restore_allowed")
        if allowed is not None:
            return "ACTIVE" if allowed else "BLOCKED"
    if category == "ACCOUNT_TOMBSTONE":
        return "PRESENT" if any(row.get(k) not in {None, "", "null"} for k in PII_KEYS) else "REDACTED"
    if category in {"ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"}:
        relevant = ("customer_reference", "user_id", *PII_KEYS)
        if any(k in row for k in relevant):
            return "PRESENT" if any(row.get(k) not in (None, "", "null") for k in relevant) else "ANONYMIZED"
    if category in {"SUPPORT_MESSAGE", "EMAIL_DELIVERY_LOG"}:
        if any(k in row for k in PII_KEYS):
            return "PRESENT" if any(row.get(k) not in (None, "", "null") for k in PII_KEYS) else "REDACTED"
    result = str(row.get("result") or row.get("state") or row.get("status") or "").strip().upper()
    if result in {"NOT_FOUND", "ABSENT", "MISSING", "DELETED", "NO_MATCH"}:
        return "ABSENT"
    if result in {"FOUND", "PRESENT", "IDENTIFIABLE", "ACTIVE", "DELETION_PENDING", "HIT"}:
        return "PRESENT"
    if result in {"ANONYMIZED", "REDACTED", "DISABLED", "BLOCKED"}:
        return result
    if row.get("status_code") is not None:
        try:
            return "ABSENT" if int(row["status_code"]) == 404 else "PRESENT" if int(row["status_code"]) == 200 else None
        except (ValueError, TypeError):
            return None
    if "rows" in row:
        try:
            return "PRESENT" if int(row["rows"]) > 0 else "ABSENT"
        except (ValueError, TypeError):
            pass
    if row.get("profile") is None and "profile" in row:
        return "ABSENT"
    if "profile" in row and isinstance(row["profile"], dict):
        return "PRESENT"
    if "download_available" in row:
        return "PRESENT" if _flag(row, "download_available") else "ABSENT"
    if category in {"USER_PROFILE", "SEARCH_INDEX_ENTRY", "SUPPORT_ATTACHMENT", "USER_EXPORT", "MARKETING_PROFILE", "CACHE_ENTRY", "SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD", "SECONDARY_IDENTIFIER"}:
        return "PRESENT"
    return None


def linked_resources_of(row: dict[str, Any]) -> tuple[str, ...]:
    """Return typed, explicit foreign-key resource links; ignore descriptions."""
    linked: set[str] = set()
    for name, value in row.items():
        if name.lower() in RESOURCE_LINK_KEYS and isinstance(value, str):
            if RESOURCE.fullmatch(value) and re.search(r"_\d", value):
                linked.add(value)
    return tuple(sorted(linked))


def identifiers_of(row: dict[str, Any]) -> tuple[str, ...]:
    identifiers: set[str] = set()
    for k, value in row.items():
        if k.lower() in IDENTITY_KEYS and value is not None and not isinstance(value, (dict, list)):
            val = str(value).strip().casefold()
            if val not in {"", "null", "none"}:
                identifiers.add(val)
    return tuple(sorted(identifiers))


def _observed_time(row: dict[str, Any], default: datetime) -> datetime:
    for key in ("observed_at", "recorded_at", "captured_at", "event_time"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                time = parse_timestamp(value, context="observation")
                if time <= default:
                    return time
            except ValueError:
                pass
    return default


def from_record(row: dict[str, Any], file: EvidenceFile, context: str = "", system: str | None = None, category: str | None = None) -> Observation | None:
    resource = row.get("resource_id") or row.get("record_id")
    if not isinstance(resource, str) or not RESOURCE.fullmatch(resource):
        return None
    guess_system, guess_category = table_class(context, resource)
    system_id = str(row.get("system") or row.get("system_id") or row.get("data_store") or system or guess_system)
    data_category = str(row.get("record_type") or row.get("data_category") or category or guess_category)
    state = state_from_record(row, data_category)
    if state is None:
        return None
    keys = identifiers_of(row)
    # Nested profile objects contain the actual identity values in service exports.
    if isinstance(row.get("profile"), dict):
        keys = tuple(sorted(set(keys) | set(identifiers_of(row["profile"]))))
    return Observation(file.file_id, _observed_time(row, file.captured_at), resource,
                       system_id, data_category, state, keys, f"{file.path.name}:{context}",
                       linked_resources=linked_resources_of(row))


def _unwrap_typed_json_row(row: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """Decode an explicit SQL `record_json` envelope, without inferring ownership.

    The envelope's resource ID and the payload's resource ID must agree. Only
    known typed identity fields (including a nested search document) are
    surfaced. Free-form text and neighboring SQL rows are never identity links.
    """
    raw = row.get("record_json")
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    resource = row.get("resource_id")
    if not isinstance(resource, str) or not RESOURCE.fullmatch(resource):
        return None
    if payload.get("resource_id") != resource:
        return None
    # SQL envelope metadata identifies the source; payload describes a record.
    origin = row.get("origin_system")
    expected_system, _ = resource_class(resource)
    if origin != expected_system:
        return None
    collection = row.get("collection_name")
    if not isinstance(collection, str) or not collection:
        return None
    expanded = dict(payload)
    expanded["system_id"] = origin
    # A nested search document contains exact identity columns, not a generic
    # textual mention. Do not recurse into metrics, events, or message bodies.
    for nested_name in ("document", "profile"):
        nested = payload.get(nested_name)
        if isinstance(nested, dict):
            for key in IDENTITY_KEYS:
                if key not in expanded and key in nested:
                    expanded[key] = nested[key]
    selector = payload.get("subject_selector")
    # Connector selector can be a direct identity token. Expressions and
    # patterns (wildcards, SQL queries) are not trustworthy identity links.
    if isinstance(selector, str) and TOKEN.fullmatch(selector.strip()):
        expanded["subject_reference"] = selector.strip()
    return expanded, collection


def scan_sql(file: EvidenceFile, *, decode_record_json: bool = False) -> list[Observation]:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    try:
        con.executescript(file.path.read_text(encoding="utf-8-sig"))
        obs = []
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            if table == "snapshot_scope":
                continue
            escaped = '"' + table.replace('"', '""') + '"'
            for row in con.execute(f"SELECT * FROM {escaped}"):
                plain = dict(row)
                if decode_record_json and "record_json" in plain:
                    unpacked = _unwrap_typed_json_row(plain)
                    # A malformed or inconsistent envelope is not state proof.
                    if unpacked is None:
                        continue
                    payload, collection = unpacked
                    item = from_record(payload, file, context=collection)
                else:
                    item = from_record(plain, file, context=table)
                if item:
                    obs.append(item)
        return obs
    finally:
        con.close()


def scan_json(file: EvidenceFile) -> list[Observation]:
    data = json.loads(file.path.read_text(encoding="utf-8-sig"))
    out: list[Observation] = []
    def visit(node: Any, context: str = "", system: str | None = None, category: str | None = None) -> None:
        if isinstance(node, list):
            for child in node:
                visit(child, context, system, category)
        elif isinstance(node, dict):
            local_system = str(node.get("data_store") or node.get("system") or system or "") or None
            local_category = str(node.get("record_type") or node.get("data_category") or category or "") or None
            if "resource_id" in node:
                record = from_record(node, file, context, local_system, local_category)
                if record:
                    out.append(record)
            for key, value in node.items():
                if isinstance(value, (dict, list)) and key != "profile":
                    # service_exports map uses system ID as the key.
                    nested_system = key if key.endswith(("_service", "_platform")) else local_system
                    nested_context = str(node.get("collection") or key)
                    visit(value, nested_context, nested_system, local_category)
    visit(data)
    return out


def _parse_pairs(line: str) -> dict[str, str]:
    return {key: value.strip('"') for key, value in PAIRS.findall(line)}


def _readback_state(event: str, keys: dict[str, str], category: str) -> str | None:
    """Infer state only from explicit completed readback payloads.

    A completed read with field values is a direct observation. Workflow reports,
    started reads, or rows lacking state-bearing fields are never treated as proof.
    """
    by_category = {
        "ANALYTICS_PROFILE": ("analytics_profile_read_completed", "analytics_identity_read_completed"),
        "SUPPORT_MESSAGE": ("support_ticket_read_completed", "support_message_read_completed"),
        "ACCOUNT_TOMBSTONE": ("tombstone_read_completed",),
        "EMAIL_DELIVERY_LOG": ("email_delivery_read_completed",),
    }
    if event not in by_category.get(category, ()):
        return None
    fields = {
        "ANALYTICS_PROFILE": ("customer_reference", "email", "phone", "email_hash"),
        "SUPPORT_MESSAGE": ("requester_email", "requester_phone", "requester_name"),
        "ACCOUNT_TOMBSTONE": ("email", "phone", "email_hash", "name", "full_name"),
        "EMAIL_DELIVERY_LOG": ("email", "customer_email", "owner_email"),
    }[category]
    # Require a positive description of current fields. Do not infer that an
    # omitted key means that the record or its personally identifying fields
    # are absent.
    if not any(field in keys for field in fields):
        return None
    present = any(str(keys.get(field, "")).strip().lower() not in {"", "null", "none", "-"}
                  for field in fields)
    if category == "ANALYTICS_PROFILE":
        return "PRESENT" if present else "ANONYMIZED"
    return "PRESENT" if present else "REDACTED"


def scan_text(file: EvidenceFile, *, enhanced_readback: bool = False) -> list[Observation]:
    text = file.path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    traces: dict[str, set[str]] = {}
    for line in lines:
        tid = TRACE.search(line)
        if tid:
            keys = _parse_pairs(line)
            traces.setdefault(tid.group(1), set()).update(v.casefold() for k, v in keys.items() if k in IDENTITY_KEYS and v.lower() != "null")
    out = []
    for i, line in enumerate(lines, 1):
        resources = RESOURCE.findall(line)
        if enhanced_readback:
            # Word-boundary prefixes also match service and event names such as
            # analytics_platform and support_ticket_read_completed. Restrict
            # enhanced reads to explicitly numbered resource IDs, preferably
            # the resource_id field from the actual completed event.
            pairs = _parse_pairs(line)
            actual = pairs.get("resource_id", "")
            if actual and RESOURCE.fullmatch(actual) and re.search(r"_\d", actual):
                resources = [actual]
            else:
                resources = [r for r in resources if re.search(r"_\d", r)]
        if not resources:
            continue
        if " | " in line and line.lstrip().startswith("-"):
            parts = [p.strip() for p in line.lstrip("- ").split("|")]
            if len(parts) >= 6:
                raw = {"system": parts[1], "resource_id": parts[2], "record_type": parts[3], "result": parts[4], "subject_reference": parts[5]}
                observation = from_record(raw, file, f"line:{i}")
                if observation:
                    out.append(observation)
                continue
        keys = _parse_pairs(line)
        tid = TRACE.search(line)
        if tid:
            inherited = traces.get(tid.group(1), set())
        else:
            inherited = set()
        event = keys.get("event", "").lower()
        if event.endswith("_started") or event in {"dependency_retry", "cache_refresh"}:
            continue
        state = None
        if keys.get("rows") == "0" or keys.get("http_status") == "404" or keys.get("result", "").lower() in {"miss", "not_found"}:
            state = "ABSENT"
        elif keys.get("rows", "0").isdigit() and int(keys.get("rows", "0")) > 0 or keys.get("result", "").lower() == "hit" or keys.get("http_status") == "200":
            state = "PRESENT"
        elif keys.get("enabled", "").lower() in {"false", "0"}:
            state = "DISABLED"
        elif keys.get("enabled", "").lower() in {"true", "1"}:
            state = "ACTIVE"
        elif keys.get("restore_allowed", "").lower() in {"false", "0"}:
            state = "BLOCKED"
        elif keys.get("restore_allowed", "").lower() in {"true", "1"}:
            state = "ACTIVE"
        elif event in {"message_pii_scan", "attachment_pii_scan", "analytics_identity_scan"}:
            state = "PRESENT"
        elif event.endswith("_completed") and keys.get("result", "").lower() in {"found", "not_found", "identifiable", "anonymized", "redacted", "blocked", "disabled"}:
            state = state_from_record(keys, resource_class(resources[0])[1])
        if state is None and enhanced_readback and resources:
            # Parse only direct readback completions, never workflow-success
            # assertions or free-form unsupported media.
            state = _readback_state(event, keys, resource_class(resources[0])[1])
        if state is None:
            continue
        matched_tokens = set(identifiers_of(keys)) | inherited
        captured = TIMESTAMP.search(line)
        time = file.captured_at
        if captured:
            try:
                time = parse_timestamp(captured.group(1), context="log")
            except ValueError:
                pass
        if time > file.captured_at:
            continue
        for resource in sorted(set(resources)):
            system, cat = resource_class(resource)
            if cat == "DATA_FLOW" and state == "PRESENT":
                state = "ACTIVE"
            if cat == "BACKUP_ARCHIVE" and state == "PRESENT":
                state = "ACTIVE"
            out.append(Observation(file.file_id, time, resource, keys.get("service", system), cat, state,
                                   tuple(sorted(matched_tokens)), f"{file.path.name}:line:{i}",
                                   linked_resources=linked_resources_of(keys)))
    return out


def _event_payload_v6(line: str) -> dict[str, str] | None:
    """Read JSON-line and key=value log payloads without substring guessing."""
    text = line.lstrip()
    match = TIMESTAMP.search(text)
    if not match:
        return None
    remainder = text[match.end():].lstrip()
    if remainder.startswith("{"):
        try:
            document = json.loads(remainder)
        except ValueError:
            return None
        if not isinstance(document, dict):
            return None
        return {k: str(v) for k, v in document.items() if isinstance(v, (str, int, float, bool)) and v is not None}
    return _parse_pairs(remainder)


def _scan_event_lines_v6(file: EvidenceFile) -> list[Observation]:
    """Correlate started queries to completed reads within exact trace IDs.

    A started event is context, never proof. No cross-trace or same-file
    ownership inference. Restorable backup candidates are ACTIVE until blocked.
    """
    lines = file.path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    parsed = []
    for line in lines:
        keys = _event_payload_v6(line)
        if not keys:
            continue
        event = keys.get("event", "").lower()
        if not event:
            continue
        m = TIMESTAMP.search(line)
        if not m:
            continue
        try:
            timestamp = parse_timestamp(m.group(1), context="structured event")
        except ValueError:
            continue
        if timestamp > file.captured_at:
            continue
        trace = keys.get("trace_id")
        parsed.append((timestamp, event, keys, trace))

    result: list[Observation] = []
    context_by_trace: dict[str, dict[str, str]] = {}
    for timestamp, event, keys, trace in parsed:
        if event.endswith("_started"):
            if trace:
                typed_context = {k: v for k, v in keys.items() if k in IDENTITY_KEYS or k in {"source"}}
                context_by_trace.setdefault(trace, {}).update(typed_context)
            continue
        rid = keys.get("resource_id", "")
        if not RESOURCE.fullmatch(rid) or not re.search(r"_\d", rid):
            continue
        system, category = resource_class(rid)
        if system == "unknown":
            continue
        merged = dict(context_by_trace.get(trace, {})) if trace else {}
        merged.update(keys)
        state: str | None = None
        if (category == "BACKUP_ARCHIVE" and event == "restore_candidate_evaluated"
                and _flag(keys, "eligible") is True and _flag(keys, "deletion_block") is False):
            state = "ACTIVE"
        elif (category == "SUPPORT_MESSAGE" and event == "contact_query_completed"
              and merged.get("source", "").lower() == "legacy_contact_index"):
            category = "SECONDARY_IDENTIFIER"
            system = "support_platform"
            state = state_from_record(keys, "SUPPORT_MESSAGE")
        elif (category == "ANALYTICS_PROFILE" and event == "contact_lookup_completed"
              and "email_opt_in" in keys):
            category = "MARKETING_PROFILE"
            state = state_from_record(keys, "SEARCH_INDEX_ENTRY")
        elif event in {"profile_lookup_completed", "index_query_completed", "audit_record_read_completed", "export_download_check_completed", "attachment_scan_completed"}:
            state = state_from_record(keys, category)
        elif event in {"analytics_profile_read_completed", "analytics_identity_read_completed", "support_ticket_read_completed", "support_message_read_completed"}:
            state = _readback_state(event, keys, category)
        if state is None:
            continue
        identifiers = identifiers_of(merged)
        result.append(Observation(file.file_id, timestamp, rid, system, category,
                                  state, identifiers, f"{file.path.name}:v6-event:{event}"))
    return result


def scan_pdf(file: EvidenceFile, *, enriched_media: bool = False, structured_attachment: bool = False) -> tuple[list[Observation], bool]:
    try:
        import pymupdf  # type: ignore
    except ImportError:
        return [], False
    with pymupdf.open(file.path) as doc:
        pages = [page.get_text(sort=structured_attachment) for page in doc]
    content = "\n".join(pages)
    if not content.strip():
        return [], False
    observations = _scan_media_text(file, content, enriched=enriched_media)
    if structured_attachment:
        observations.extend(_scan_support_attachment_document(file, content, observations))
    return observations, True


def scan_image(file: EvidenceFile, *, enriched_media: bool = False) -> tuple[list[Observation], bool]:
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
        content = pytesseract.image_to_string(Image.open(file.path))
    except (ImportError, FileNotFoundError, OSError):
        return [], False
    except Exception:
        return [], False
    return (_scan_media_text(file, content, enriched=enriched_media), True) if content.strip() else ([], False)


def _scan_support_attachment_document(file: EvidenceFile, content: str,
                                     existing: list[Observation]) -> list[Observation]:
    """Read a *single-subject* support attachment reference across PDF lines.

    Unlike a profile database, an evidentiary PDF can lay out its support ID and
    the customer identity on different lines. Only a typed `support_N` following
    an explicit support reference, a unique customer email and an attachment
    section are sufficient. No generic page proximity, PDF filename inference,
    unrelated phone numbers, or workflow completion message can assert ownership.
    The file's capture is proof the still-identifiable attachment was observed;
    this is never absence or proof that deletion completed.
    """
    if not re.search(r"(?im)^\s*(?:support reference|support case|support ticket)\b", content):
        return []
    if not re.search(r"\battachment\b", content, re.I):
        return []
    if not re.search(r"\bcustomer request\b|\bcustomer details\b|\bcustomer information\b", content, re.I):
        return []
    resources: set[str] = set()
    # Support ID must be on a line explicitly labelled as a support reference.
    for line in content.splitlines():
        if re.match(r"\s*(?:support reference|support case|support ticket)\b", line, re.I):
            resources.update(re.findall(r"\bsupport_\d+\b", line, re.I))
    if len(resources) != 1:
        return []
    # Only an unambiguous *single subject* document is eligible. If multiple
    # customer emails appear, do not attach the document to a random person.
    emails = {m.casefold() for m in re.findall(r"(?<![\w@.+-])[\w.+-]+@[\w.-]+(?![\w@.+-])", content)}
    if len(emails) != 1:
        return []
    # Do not treat a fully redacted attachment or a stated removal as exposed
    # PII. This is a conservative recognition gate, not a broad keyword model.
    if re.search(r"(?im)^\s*(?:attachment\s*(?:status|state)|upload\s*status)\s*[:=]\s*(?:deleted|removed|absent|redacted)\b", content):
        return []
    rid = next(iter(resources))
    sig = (rid, "PRESENT", tuple(sorted(emails)))
    if any((obs.resource_id, obs.state, obs.identifiers) == sig for obs in existing):
        return []
    return [Observation(file.file_id, file.captured_at, rid,
                        "support_platform", "SUPPORT_ATTACHMENT", "PRESENT",
                        tuple(sorted(emails)), f"{file.path.name}:support-attachment-document")]


def _scan_media_text(file: EvidenceFile, content: str, *, enriched: bool = False) -> list[Observation]:
    """Conservatively extract explicitly formatted resource rows only."""
    out = []
    for i, line in enumerate(content.splitlines(), start=1):
        resources = RESOURCE.findall(line)
        for resource in set(resources):
            system, category = resource_class(resource)
            upper = line.upper()
            if category == "SUPPORT_MESSAGE" and "SUPPORT_ATTACHMENT" in upper:
                category = "SUPPORT_ATTACHMENT"
            status = next((x for x in ("NOT_FOUND", "ANONYMIZED", "REDACTED", "DISABLED", "BLOCKED", "IDENTIFIABLE", "FOUND", "ACTIVE") if re.search(r"\b"+x+r"\b", upper)), None)
            if not status:
                continue
            values = {x.casefold() for x in TOKEN.findall(line)}
            if not values:
                continue
            state = "ABSENT" if status == "NOT_FOUND" else "PRESENT" if status in {"FOUND", "IDENTIFIABLE"} else status
            out.append(Observation(file.file_id, file.captured_at, resource, system, category,
                                   state, tuple(sorted(values)), f"{file.path.name}:text-line:{i}"))
    if enriched:
        out.extend(_scan_structured_media_blocks(file, content, out))
    return out


def _scan_structured_media_blocks(file: EvidenceFile, content: str,
                                  existing: list[Observation]) -> list[Observation]:
    """Add explicit multi-line evidence, never infer a user from nearby unrelated rows.

    A candidate must include exactly one numbered resource, an explicit identity,
    and an unambiguous status in a short record block. No missing status is
    interpreted as ABSENT. This is a conservative supplement, not generic OCR.
    """
    found: list[Observation] = []
    already = {(o.resource_id, o.state, o.identifiers) for o in existing}
    # Blank lines / separator rules are record boundaries, not page boundaries.
    blocks = re.split(r"(?:\n\s*\n|\n[-=]{3,}\n)", content)
    for block in blocks:
        lines = block.splitlines()
        if not 1 <= len(lines) <= 9 or len(block) > 1800:
            continue
        resources = set(RESOURCE.findall(block))
        numbered = {r for r in resources if re.search(r"_\d", r)}
        if len(numbered) != 1:
            continue
        resource = next(iter(numbered))
        tokens = {x.casefold() for x in TOKEN.findall(block)}
        if not tokens:
            continue
        # Explicit state/result/status field only; ordinary prose can describe
        # historical events instead of the state at capture time.
        matches = re.findall(r"(?im)^\s*(?:state|status|result|lookup_result)\s*[:=]\s*(NOT_FOUND|ABSENT|DELETED|FOUND|PRESENT|IDENTIFIABLE|ANONYMIZED|REDACTED|DISABLED|BLOCKED|ACTIVE)\b", block)
        if len(set(x.upper() for x in matches)) != 1:
            continue
        raw = matches[0].upper()
        state = "ABSENT" if raw in {"NOT_FOUND", "ABSENT", "DELETED"} else "PRESENT" if raw in {"FOUND", "PRESENT", "IDENTIFIABLE"} else raw
        system, category = resource_class(resource)
        if category == "SUPPORT_MESSAGE" and re.search(r"\bsupport_attachment\b|\battachment\b", block, re.I):
            category = "SUPPORT_ATTACHMENT"
        if category == "DATA_FLOW" and state == "PRESENT":
            state = "ACTIVE"
        if category == "BACKUP_ARCHIVE" and state == "PRESENT":
            state = "ACTIVE"
        item = Observation(file.file_id, file.captured_at, resource, system, category,
                           state, tuple(sorted(tokens)), f"{file.path.name}:structured-block",
                           linked_resources=linked_resources_of(_parse_pairs(block)))
        signature = (item.resource_id, item.state, item.identifiers)
        if signature not in already:
            found.append(item)
            already.add(signature)
    return found


@dataclass(frozen=True, slots=True)
class EvidenceIndex:
    observations: tuple[Observation, ...]
    unreadable_files: tuple[str, ...]
    examined_files: tuple[str, ...]
    unparsed_media: tuple[str, ...] = ()
    file_captures: dict[str, datetime] = field(default_factory=dict)


def build_index(files: Iterable[EvidenceFile], *, enhanced_readback: bool = False, enriched_media: bool = False,
                decode_sql_json: bool = False, structured_attachment: bool = False, structured_event_v6: bool = False) -> EvidenceIndex:
    observations = []
    unsupported = []
    examined = []
    unparsed_media = []
    file_captures = {}
    for file in files:
        file_captures[file.file_id] = file.captured_at
        suffix = file.path.suffix.lower()
        if suffix == ".sql":
            rows = scan_sql(file, decode_record_json=decode_sql_json)
            ok = True
        elif suffix == ".json":
            rows = scan_json(file)
            ok = True
        elif suffix in {".log", ".txt"}:
            rows = scan_text(file, enhanced_readback=enhanced_readback)
            if structured_event_v6:
                v6 = _scan_event_lines_v6(file)
                upgrades = {(o.file_id, o.resource_id, o.observed_at) for o in v6}
                rows = [o for o in rows if (o.file_id, o.resource_id, o.observed_at) not in upgrades] + v6
            ok = True
        elif suffix == ".pdf":
            rows, ok = scan_pdf(file, enriched_media=enriched_media, structured_attachment=structured_attachment)
        elif suffix == ".png":
            rows, ok = scan_image(file, enriched_media=enriched_media)
        else:
            rows, ok = [], False
        if ok:
            examined.append(file.file_id)
            observations.extend(rows)
            if suffix in {".pdf", ".png"} and not rows:
                unparsed_media.append(file.file_id)
        else:
            unsupported.append(file.file_id)
    return EvidenceIndex(tuple(observations), tuple(unsupported), tuple(examined), tuple(unparsed_media), file_captures)
