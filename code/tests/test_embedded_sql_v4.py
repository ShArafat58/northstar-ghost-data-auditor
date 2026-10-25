"""Synthetic, label-independent regression tests for embedded-JSON SQL envelopes.

Tests do not import sample_output.csv or any hackathon answer labels.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline import plan_case
from identity import ResolvedIdentity
from loaders import DeletionRequest, EvidenceFile
from observations import build_index, scan_sql
from validator import validate_prediction

AT = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
REQUESTED = AT - timedelta(days=10)
IDENTITY = ResolvedIdentity("user_53", "subject@example.com", ("subject@example.com",), (), (), (), "snapshot")
RULES = (
    {"data_category": "USER_PROFILE", "condition": "Always", "required_action": "DELETE"},
    {"data_category": "SEARCH_INDEX_ENTRY", "condition": "Always", "required_action": "DELETE"},
    {"data_category": "ANALYTICS_PROFILE", "condition": "attributable to user", "required_action": "ANONYMIZE"},
    {"data_category": "SECURITY_AUDIT_LOG", "condition": "within retention", "required_action": "RETAIN"},
)


def req(cutoff: datetime = AT) -> DeletionRequest:
    return DeletionRequest("synthetic", "subject@example.com", REQUESTED,
                           "NOT_STARTED", REQUESTED, cutoff, "Please delete my account")


def create_snapshot(path: Path, records: list[tuple[str, str, str, object]], *, table: str = "customer_data_read_replica") -> None:
    con = sqlite3.connect(str(path))
    try:
        con.execute(f'CREATE TABLE "{table}" (resource_id TEXT PRIMARY KEY, origin_system TEXT, collection_name TEXT, record_json TEXT, updated_at TEXT)')
        for rid, system, collection, data in records:
            encoded = json.dumps(data) if not isinstance(data, str) else data
            con.execute(f'INSERT INTO "{table}" VALUES (?,?,?,?,?)', (rid, system, collection, encoded, AT.isoformat()))
        con.commit()
    finally:
        con.close()
    # The SQL fixture must be real SQL (not a binary sqlite database).
    con = sqlite3.connect(str(path))
    try:
        sql = "\n".join(con.iterdump())
    finally:
        con.close()
    path.write_text(sql, encoding="utf-8")


class EmbeddedSqlV4Tests(unittest.TestCase):
    def test_replica_json_account_identity_is_recovered(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "snapshot.sql"
            create_snapshot(f, [("account_994", "account_service", "customer_profiles", {
                "resource_id": "account_994", "customer_reference": "user_53",
                "email": "subject@example.com", "account_status": "ACTIVE"})])
            file = EvidenceFile("evidence_test", f, "test", AT)
            old_index = build_index([file], enhanced_readback=True)
            new_index = build_index([file], enhanced_readback=True, decode_sql_json=True)
            old = plan_case(req(), IDENTITY, old_index, RULES, variant="readback-v2")
            new = plan_case(req(), IDENTITY, new_index, RULES, variant="embedded-sql-v4")
            self.assertEqual(old.resources_seen, 0)
            self.assertEqual(new.status, "INCOMPLETE")
            self.assertEqual(new.resources_seen, 1)
            self.assertEqual(new.steps[0]["resource_id"], "account_994")
            self.assertEqual(new.steps[0]["evidence_id"], "evidence_test")
            validate_prediction(new.status, new.steps, evidence_ids=frozenset({"evidence_test"}))

    def test_nested_document_email_is_a_typed_identity(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "snapshot.sql"
            create_snapshot(f, [("search_1007", "search_service", "customer_search_documents", {
                "resource_id": "search_1007", "index_name": "directory",
                "document": {"email": "SUBJECT@EXAMPLE.COM", "customer_reference": "user_53", "display_name": "Subject"}})])
            result = scan_sql(EvidenceFile("e", f, "test", AT), decode_record_json=True)
            self.assertEqual(len(result), 1)
            self.assertIn("subject@example.com", result[0].identifiers)
            self.assertEqual(result[0].category, "SEARCH_INDEX_ENTRY")
            self.assertEqual(result[0].state, "PRESENT")

    def test_deletion_relevant_analytics_not_flattened_from_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "snapshot.sql"
            create_snapshot(f, [("analytics_4440", "analytics_platform", "customer_analytics_reports", {
                "resource_id": "analytics_4440", "user_id": "user_53", "email": "subject@example.com",
                "metrics": {"page_title": "other@example.com"}})])
            result = scan_sql(EvidenceFile("e", f, "test", AT), decode_record_json=True)
            self.assertEqual(result[0].state, "PRESENT")
            self.assertEqual(result[0].category, "ANALYTICS_PROFILE")
            self.assertNotIn("other@example.com", result[0].identifiers)

    def test_direct_connector_subject_selector_identity(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "snapshot.sql"
            create_snapshot(f, [("data_flow_7", "integration_service", "connector_configurations", {
                "resource_id": "data_flow_7", "subject_selector": "user_53", "enabled": True})])
            result = scan_sql(EvidenceFile("e", f, "test", AT), decode_record_json=True)
            self.assertEqual(result[0].state, "ACTIVE")
            self.assertIn("user_53", result[0].identifiers)

    def test_mismatched_embedded_resource_id_does_not_create_observation(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "snapshot.sql"
            create_snapshot(f, [("account_994", "account_service", "customer_profiles", {
                "resource_id": "account_995", "customer_reference": "user_53", "account_status": "ACTIVE"})])
            self.assertEqual(scan_sql(EvidenceFile("e", f, "test", AT), decode_record_json=True), [])

    def test_invalid_embedded_json_cannot_be_used_as_proof(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "snapshot.sql"
            create_snapshot(f, [("account_994", "account_service", "customer_profiles", '{"bad"')])
            self.assertEqual(scan_sql(EvidenceFile("e", f, "test", AT), decode_record_json=True), [])

    def test_exact_user_identity_prevents_neighbouring_user_collision(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "snapshot.sql"
            create_snapshot(f, [("account_994", "account_service", "customer_profiles", {
                "resource_id": "account_994", "customer_reference": "user_530", "email": "other@example.com",
                "account_status": "ACTIVE"})])
            index = build_index([EvidenceFile("e", f, "test", AT)], enhanced_readback=True, decode_sql_json=True)
            pred = plan_case(req(), IDENTITY, index, RULES, variant="embedded-sql-v4")
            self.assertEqual(pred.resources_seen, 0)

    def test_future_sql_envelope_is_excluded_by_audit_cutoff(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "snapshot.sql"
            create_snapshot(f, [("account_994", "account_service", "customer_profiles", {
                "resource_id": "account_994", "customer_reference": "user_53", "account_status": "ACTIVE"})])
            future_file = EvidenceFile("e", f, "test", AT + timedelta(hours=1))
            index = build_index([future_file], enhanced_readback=True, decode_sql_json=True)
            pred = plan_case(req(), IDENTITY, index, RULES, variant="embedded-sql-v4")
            self.assertEqual(pred.resources_seen, 0)
            self.assertEqual(pred.steps, [])


if __name__ == "__main__":
    unittest.main()
