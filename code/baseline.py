"""Conservative sample-baseline planner, independent of labelled reference outputs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from identity import ResolvedIdentity
from loaders import DeletionRequest
from observations import EvidenceIndex, Observation
from validator import sort_steps

# These are intent expressions, not canonical case IDs or expected labels.
POSITIVE = re.compile(
    r"\b(?:delete|deleted|deleting|deletion|erase|erased|erasure|remove my (?:account|profile|data|personal)|"
    r"close (?:my|this|the) account|account closure|permanently close|right to be forgotten|"
    r"eliminar|eliminaci[oó]n|borrar|supprimer|suppression|effacer|effacement|"
    r"l[oö]schen|datenschutzl[oö]schung)\b|削除|消去|退会|削除して",
    re.I,
)
NEGATIVE = re.compile(
    r"\b(?:do not|don't|dont|never|please not|not asking to|without|instead of)\s+(?:want\s+to\s+)?(?:delete|deleting|erase|erasing|remove|removing|close|closing)\b|"
    r"\b(?:keep|retain)\s+my\s+account\b",
    re.I,
)

DELETION = frozenset({"USER_PROFILE", "SECONDARY_IDENTIFIER", "SEARCH_INDEX_ENTRY", "CACHE_ENTRY", "SUPPORT_ATTACHMENT", "USER_EXPORT", "MARKETING_PROFILE"})
REDACTION = frozenset({"SUPPORT_MESSAGE", "EMAIL_DELIVERY_LOG", "ACCOUNT_TOMBSTONE"})
ANONYMIZATION = frozenset({"ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"})
RETENTION = frozenset({"SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD"})


@dataclass(frozen=True, slots=True)
class CasePlan:
    case_id: str
    status: str
    steps: list[dict[str, object]]
    warnings: tuple[str, ...]
    resources_seen: int


def authorizes_deletion(message: str) -> bool:
    if NEGATIVE.search(message):
        return False
    return bool(POSITIVE.search(message))


def _identity_set(identity: ResolvedIdentity) -> frozenset[str]:
    return frozenset({identity.user_id.casefold(), *(v.casefold() for v in identity.emails),
                      *(v.casefold() for v in identity.phones), *(v.casefold() for v in identity.email_hashes)})


def _case_observations(index: EvidenceIndex, identity: ResolvedIdentity, cutoff: datetime, *, expand_links: bool = False) -> dict[tuple[str, str], list[Observation]]:
    keys = _identity_set(identity)
    known_resources: set[str] = set()
    eligible = [o for o in index.observations if o.observed_at <= cutoff]
    for obs in eligible:
        if keys.intersection(obs.identifiers):
            known_resources.add(obs.resource_id)
    # A record may become anonymous or absent; follow its grounded resource ID.
    # For V3 only: propagate via *explicit* resource-to-resource foreign keys.
    # Never link merely because two resources share a file, trace, or timestamp.
    if expand_links:
        changed = True
        while changed:
            changed = False
            for obs in eligible:
                if not obs.linked_resources:
                    continue
                edges = set(obs.linked_resources)
                if obs.resource_id in known_resources or known_resources.intersection(edges):
                    additions = {obs.resource_id, *edges} - known_resources
                    if additions:
                        known_resources.update(additions)
                        changed = True
    matches = [o for o in eligible if o.resource_id in known_resources]
    by_resource: dict[tuple[str, str], list[Observation]] = {}
    for obs in matches:
        by_resource.setdefault((obs.system_id, obs.resource_id), []).append(obs)
    return by_resource


def _select_state(group: list[Observation], variant: str) -> Observation:
    # Latest read-back beats an older record. For simultaneous replica views,
    # a surviving PRESENT copy must not be erased by a missing primary copy.
    ordered = sorted(group, key=lambda o: (o.observed_at, o.file_id))
    latest = ordered[-1]
    if variant == "replica-safe" and latest.state == "ABSENT":
        presence = [o for o in ordered if o.state == "PRESENT" and o.observed_at >= latest.observed_at - timedelta(minutes=60)]
        if presence:
            return presence[-1]
    return latest


def _step(phase: str, system: str, resource: str, operation: str, state: str, evidence: str = "") -> dict[str, object]:
    return {"step_no": 0, "phase": phase, "system_id": system, "resource_id": resource,
            "operation": operation, "state": state, "evidence_id": evidence}


def _policy_action(category: str, rules: tuple[dict[str, str], ...]) -> str | None:
    # Do not invent or apply a conditional policy if its required condition is unknown.
    entries = [row for row in rules if row["data_category"] == category]
    if not entries:
        return None
    known = {row["required_action"].upper() for row in entries}
    if len(known) != 1:
        return None
    return next(iter(known))


def plan_case(request: DeletionRequest, identity: ResolvedIdentity | None,
              evidence: EvidenceIndex, rules: tuple[dict[str, str], ...],
              *, variant: str = "latest") -> CasePlan:
    if variant not in {"latest", "replica-safe", "intent-v2", "readback-v2", "verification-v3", "embedded-sql-v4", "media-evidence-v5", "structured-event-v6"}:
        raise ValueError("Unknown A/B variant")
    # Isolate the request-intent hypothesis. All evidence selection and policy
    # logic remain exactly the same as the original "latest" baseline.
    if variant in {"intent-v2", "readback-v2", "verification-v3", "embedded-sql-v4", "media-evidence-v5", "structured-event-v6"}:
        from intent_v2 import authorizes_deletion_v2
        authorized = authorizes_deletion_v2(request.request_text)
    else:
        authorized = authorizes_deletion(request.request_text)
    if not authorized:
        return CasePlan(request.case_id, "NOT_APPLICABLE", [], (), 0)
    if identity is None:
        return CasePlan(request.case_id, "COMPLETE", [], ("UNRESOLVED_IDENTITY: manual review required",), 0)

    groups = _case_observations(evidence, identity, request.audit_at,
                                expand_links=variant == "verification-v3")
    findings: list[dict[str, object]] = []
    remediation: list[dict[str, object]] = []
    verify: list[dict[str, object]] = []
    warnings: list[str] = []
    for (_, _), history in sorted(groups.items()):
        if variant == "structured-event-v6":
            # A future-exported log may describe an event that occurred before
            # the cutoff, so its resource ID can inform a *planned* check.
            # It cannot be cited as proof available at the audit cutoff.
            available_history = [item for item in history if evidence.file_captures.get(item.file_id, item.observed_at) <= request.audit_at]
            observed = _select_state(available_history or history, variant)
            if not available_history and observed.state in {"PRESENT", "ACTIVE"}:
                warnings.append(f"FUTURE_SOURCE_NOT_FINDING_PROOF: {observed.resource_id}")
                continue
        else:
            observed = _select_state(history, variant)
        sys_id, rid, category, state = observed.system_id, observed.resource_id, observed.category, observed.state
        proof = observed.file_id
        # Pre-request data can establish an ID link, but cannot prove a post-request failure.
        if observed.observed_at < request.requested_at and state in ({"PRESENT", "ACTIVE", "ABSENT"} if variant == "structured-event-v6" else {"PRESENT", "ACTIVE"}):
            warnings.append(f"PRE_REQUEST_ONLY: {rid}")
            continue
        policy = _policy_action(category, rules)
        if category == "DATA_FLOW":
            if state in {"ACTIVE", "PRESENT"}:
                findings.append(_step("FINDING", sys_id, rid, "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", proof))
                remediation.append(_step("REMEDIATION", sys_id, rid, "DISABLE_DATA_FLOW", "DISABLED"))
                verify.append(_step("VERIFICATION", sys_id, rid, "VERIFY_DATA_FLOW_DISABLED", "DISABLED"))
            elif state == "DISABLED":
                verify.append(_step("VERIFICATION", sys_id, rid, "VERIFY_DATA_FLOW_DISABLED", "DISABLED", proof))
            continue
        if category == "BACKUP_ARCHIVE":
            if state in {"ACTIVE", "PRESENT"}:
                findings.append(_step("FINDING", sys_id, rid, "REPORT_RESTORE_RISK", "ACTIVE", proof))
                remediation.append(_step("REMEDIATION", sys_id, rid, "BLOCK_RESTORE", "BLOCKED"))
                verify.append(_step("VERIFICATION", sys_id, rid, "VERIFY_RESTORE_BLOCKED", "BLOCKED"))
            elif state == "BLOCKED":
                verify.append(_step("VERIFICATION", sys_id, rid, "VERIFY_RESTORE_BLOCKED", "BLOCKED", proof))
            continue
        if not policy:
            warnings.append(f"UNCLASSIFIED_OR_UNKNOWN_POLICY: {rid} ({category})")
            continue
        if policy == "RETAIN":
            if state in {"PRESENT", "REDACTED"}:
                verify.append(_step("VERIFICATION", sys_id, rid, "VERIFY_RECORD_PRESENT", "PRESENT", proof))
            elif state == "ABSENT":
                warnings.append(f"MISSING_POLICY_RETAINED_RECORD: {rid}")
            continue
        desired, operation, checker = {
            "DELETE": ("ABSENT", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
            "REDACT": ("REDACTED", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
            "ANONYMIZE": ("ANONYMIZED", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
        }.get(policy, (None, None, None))
        if not desired or not operation or not checker:
            warnings.append(f"UNSUPPORTED_ACTION: {rid} ({policy})")
            continue
        if state == desired:
            verify.append(_step("VERIFICATION", sys_id, rid, checker, desired, proof))
        elif state == "PRESENT":
            finding = "REPORT_REMAINING_RECORD" if policy == "DELETE" else "REPORT_EXPOSED_PII"
            findings.append(_step("FINDING", sys_id, rid, finding, "PRESENT", proof))
            remediation.append(_step("REMEDIATION", sys_id, rid, operation, desired))
            verify.append(_step("VERIFICATION", sys_id, rid, checker, desired))
        else:
            warnings.append(f"UNINTERPRETED_STATE: {rid} ({state})")

    incomplete = bool(findings)
    if incomplete and variant == "structured-event-v6":
        # An exact typed foreign key can identify another system resource
        # that still requires verification. A planned check is NOT proof of
        # absence; never generate one in an otherwise COMPLETE case.
        from observations import resource_class
        verified_resources = {item["resource_id"] for item in verify}
        observed_resources = {resource_id for (_, resource_id) in groups}
        for history in groups.values():
            for entry in history:
                if evidence.file_captures.get(entry.file_id, entry.observed_at) > request.audit_at:
                    continue
                for linked in entry.linked_resources:
                    system_id, data_category = resource_class(linked)
                    if (data_category == "USER_PROFILE" and linked not in verified_resources
                            and linked not in observed_resources):
                        verify.append(_step("VERIFICATION", system_id, linked,
                                            "VERIFY_RECORD_ABSENT", "ABSENT"))
                        verified_resources.add(linked)
    if not groups:
        warnings.append("NO_IDENTITY_LINKED_RESOURCES: empty proof set")
    if not incomplete and variant == "structured-event-v6":
        # A verification cited in a COMPLETE judgement must be supported by
        # a file available at the audit cutoff, not only by a later export.
        verify = [step for step in verify if (
            step["evidence_id"] and evidence.file_captures.get(str(step["evidence_id"]), request.audit_at) <= request.audit_at
        )]
    steps = findings + remediation + verify if incomplete else verify
    if incomplete:
        # Future verification steps never cite current proof, including checks
        # of resources which are already compliant.
        for step in steps:
            if step["phase"] == "VERIFICATION":
                step["evidence_id"] = ""
    ordered = sort_steps(steps)
    for position, item in enumerate(ordered, 1):
        item["step_no"] = position
    return CasePlan(request.case_id, "INCOMPLETE" if incomplete else "COMPLETE", ordered,
                    tuple(sorted(set(warnings))), len(groups))
