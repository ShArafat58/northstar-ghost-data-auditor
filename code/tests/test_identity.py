"""Deterministic regression tests for cutoff-aware identity matching."""

from __future__ import annotations

import hashlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from identity import AmbiguousIdentityError, IdentityResolver
from loaders import IdentityChange, UserIdentity


def utc(day: int) -> datetime:
    return datetime(2026, 9, day, tzinfo=timezone.utc)


class IdentityResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = UserIdentity(
            user_id="user_example",
            name="Example",
            email="new@example.test",
            phone="+123456789",
            email_hash="",
            updated_at=utc(5),
        )
        self.change = IdentityChange(
            audit_id="audit_example",
            user_id="user_example",
            event_time=utc(4),
            field_name="email",
            old_value="old@example.test",
            new_value="new@example.test",
        )

    def test_current_email_case_insensitive(self) -> None:
        resolver = IdentityResolver((self.profile,), (self.change,))
        result = resolver.resolve("  NEW@EXAMPLE.TEST ", utc(6))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.user_id, "user_example")
        self.assertEqual(result.matched_via, "snapshot")

    def test_historical_email_links_to_same_account(self) -> None:
        resolver = IdentityResolver((self.profile,), (self.change,))
        result = resolver.resolve("old@example.test", utc(6))
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.user_id, "user_example")
        self.assertEqual(result.matched_via, "historical")
        self.assertIn("new@example.test", result.emails)
        self.assertIn("old@example.test", result.emails)
        self.assertIn(
            hashlib.sha256(b"old@example.test").hexdigest(), result.email_hashes
        )
        self.assertEqual(result.audit_ids, ("audit_example",))

    def test_future_identity_change_cannot_resolve_case(self) -> None:
        resolver = IdentityResolver((self.profile,), (self.change,))
        self.assertIsNone(resolver.resolve("old@example.test", utc(3)))

    def test_duplicate_owner_is_not_silently_selected(self) -> None:
        another = UserIdentity(
            user_id="user_second",
            name="Second",
            email="new@example.test",
            phone="",
            email_hash="",
            updated_at=utc(5),
        )
        resolver = IdentityResolver((self.profile, another), (self.change,))
        with self.assertRaises(AmbiguousIdentityError):
            resolver.resolve("new@example.test", utc(6))


if __name__ == "__main__":
    unittest.main()
