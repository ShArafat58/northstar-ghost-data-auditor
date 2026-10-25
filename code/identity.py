"""Time-bounded identity resolution across current and historical identifiers."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from loaders import IdentityChange, UserIdentity


IDENTIFIER_FIELDS = frozenset({"email", "phone", "email_hash"})


def normalize_email(value: str) -> str:
    """Normalize address casing/outer whitespace without altering its local-part syntax."""
    return value.strip().casefold()


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    user_id: str
    current_email_at_audit: str | None
    emails: tuple[str, ...]
    phones: tuple[str, ...]
    email_hashes: tuple[str, ...]
    audit_ids: tuple[str, ...]
    matched_via: str


class AmbiguousIdentityError(ValueError):
    """The supplied address links to multiple accounts before the audit cutoff."""


class IdentityResolver:
    """Use only identity observations timestamped at or before the case cutoff."""

    def __init__(
        self,
        identities: tuple[UserIdentity, ...],
        changes: tuple[IdentityChange, ...],
    ) -> None:
        self._profiles = {profile.user_id: profile for profile in identities}
        self._changes_by_user: dict[str, list[IdentityChange]] = defaultdict(list)
        for change in changes:
            if change.user_id not in self._profiles:
                raise ValueError(f"Unknown user in identity audit: {change.audit_id}")
            if change.field_name not in IDENTIFIER_FIELDS:
                raise ValueError(f"Unsupported identity field: {change.field_name}")
            self._changes_by_user[change.user_id].append(change)
        for events in self._changes_by_user.values():
            events.sort(key=lambda event: (event.event_time, event.audit_id))

    def resolve(self, email: str, audit_at: datetime) -> ResolvedIdentity | None:
        """Resolve a case email, including historic aliases, without future leakage."""
        key = normalize_email(email)
        if not key:
            return None

        matches: dict[str, str] = {}
        for user_id, profile in self._profiles.items():
            if profile.updated_at <= audit_at and normalize_email(profile.email) == key:
                matches[user_id] = "snapshot"
            for event in self._changes_by_user.get(user_id, ()):
                if event.event_time > audit_at:
                    break
                if event.field_name == "email" and key in (
                    normalize_email(event.old_value), normalize_email(event.new_value)
                ):
                    matches.setdefault(user_id, "historical")

        if len(matches) > 1:
            raise AmbiguousIdentityError(
                "The request email maps to multiple users at the audit cutoff"
            )
        if not matches:
            return None
        user_id, matched_via = next(iter(matches.items()))
        profile = self._profiles[user_id]
        observed: dict[str, set[str]] = {field: set() for field in IDENTIFIER_FIELDS}
        current_email: str | None = None

        if profile.updated_at <= audit_at:
            for field in IDENTIFIER_FIELDS:
                value = getattr(profile, field)
                if value:
                    observed[field].add(normalize_email(value) if field == "email" else value)
            current_email = normalize_email(profile.email)

        events_in_scope = [
            event for event in self._changes_by_user.get(user_id, ())
            if event.event_time <= audit_at
        ]
        for event in events_in_scope:
            for value in (event.old_value, event.new_value):
                if value:
                    observed[event.field_name].add(
                        normalize_email(value) if event.field_name == "email" else value
                    )

        email_events = [event for event in events_in_scope if event.field_name == "email"]
        if email_events:
            latest_event = email_events[-1]
            snapshot_is_newer = (
                profile.updated_at <= audit_at
                and profile.updated_at > latest_event.event_time
            )
            if latest_event.new_value and not snapshot_is_newer:
                current_email = normalize_email(latest_event.new_value)

        # The supplied email hashes are SHA-256 of normalized addresses.
        # Include hashes for historical aliases so hash-only evidence can be linked.
        for known_email in observed["email"]:
            observed["email_hash"].add(
                hashlib.sha256(known_email.encode("utf-8")).hexdigest()
            )

        return ResolvedIdentity(
            user_id=user_id,
            current_email_at_audit=current_email,
            emails=tuple(sorted(observed["email"])),
            phones=tuple(sorted(observed["phone"])),
            email_hashes=tuple(sorted(observed["email_hash"])),
            audit_ids=tuple(event.audit_id for event in events_in_scope),
            matched_via=matched_via,
        )
