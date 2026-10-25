"""Evidence-driven, sample-answer-independent tests for multi-line PDF support attachments."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from baseline import plan_case
from identity import ResolvedIdentity
from loaders import DeletionRequest, EvidenceFile
from observations import build_index, scan_pdf
from validator import validate_prediction

AT = datetime(2026, 9, 12, 7, 30, tzinfo=timezone.utc)
IDENTITY = ResolvedIdentity("user_07", "priya.raman@gmail.com", ("priya.raman@gmail.com",),
                            ("+91 98765 73142",), (), (), "snapshot")
RULES = ({"data_category": "SUPPORT_ATTACHMENT", "condition": "Subject only", "required_action": "DELETE"},)


def request(date=None):
    t = date or (AT - timedelta(days=5))
    return DeletionRequest("synthetic", "priya.raman@gmail.com", t,
                           "PARTIALLY_COMPLETE", t, AT, "Please delete my account and support attachment")


def pdf_data(content: str, cap=AT):
    import pymupdf
    td = tempfile.TemporaryDirectory()
    path = Path(td.name) / "attachment.pdf"
    doc = pymupdf.open()
    # New pages preserve record boundaries, but the attachment text can flow
    # across lines and pages as in a generated support ticket PDF.
    for page_text in content.split("[PAGE]"):
        p = doc.new_page()
        y = 72
        for line in page_text.splitlines():
            if line:
                p.insert_text((72, y), line, fontsize=10)
            y += 16
    doc.save(path)
    doc.close()
    return td, EvidenceFile("evidence_test", path, "support_platform", cap)


DOCUMENT = """DELIVERY ADDRESS CORRECTION REQUEST
Support reference CS-4827 / support_001
Customer request
Detail                              Priya Raman
Detail                              priya.raman@gmail.com
Detail                              +91 98765 73142
[PAGE]
Continued - Attachment properties and upload history
Document: identity verification attachment
"""


class MediaEvidenceV5Tests(unittest.TestCase):
    def test_multiline_pdf_creates_typed_support_attachment(self):
        td, f = pdf_data(DOCUMENT)
        with td:
            result, ok = scan_pdf(f, structured_attachment=True)
            self.assertTrue(ok)
            self.assertEqual(len(result), 1)
            obs = result[0]
            self.assertEqual((obs.resource_id, obs.category, obs.state),
                             ("support_001", "SUPPORT_ATTACHMENT", "PRESENT"))
            self.assertEqual(obs.identifiers, ("priya.raman@gmail.com",))
            self.assertEqual(obs.file_id, "evidence_test")

    def test_v4_control_is_unchanged_and_v5_is_grounded(self):
        td, f = pdf_data(DOCUMENT)
        with td:
            old = build_index([f], enhanced_readback=True, decode_sql_json=True)
            new = build_index([f], enhanced_readback=True, decode_sql_json=True, structured_attachment=True)
            self.assertEqual(len(old.observations), 0)
            self.assertEqual(len(new.observations), 1)
            prior = plan_case(request(), IDENTITY, old, RULES, variant="embedded-sql-v4")
            current = plan_case(request(), IDENTITY, new, RULES, variant="media-evidence-v5")
            self.assertEqual(prior.status, "COMPLETE")
            self.assertEqual(current.status, "INCOMPLETE")
            self.assertEqual([s["operation"] for s in current.steps],
                             ["REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"])
            self.assertEqual(current.steps[0]["evidence_id"], "evidence_test")
            validate_prediction(current.status, current.steps, evidence_ids=frozenset({"evidence_test"}))

    def test_multisubject_pdf_rejected(self):
        td, f = pdf_data(DOCUMENT.replace("[PAGE]", "Detail other.customer@example.com\n[PAGE]"))
        with td:
            obs, ok = scan_pdf(f, structured_attachment=True)
            self.assertTrue(ok)
            self.assertFalse(obs)

    def test_two_different_support_ids_rejected(self):
        td, f = pdf_data(DOCUMENT.replace("Support reference CS-4827 / support_001", "Support reference CS-4827 / support_001 and support_002"))
        with td:
            obs, ok = scan_pdf(f, structured_attachment=True)
            self.assertTrue(ok)
            self.assertFalse(obs)

    def test_no_support_reference_never_guesses_resource(self):
        td, f = pdf_data(DOCUMENT.replace("Support reference CS-4827 / support_001", "Customer reference support_001"))
        with td:
            obs, ok = scan_pdf(f, structured_attachment=True)
            self.assertTrue(ok)
            self.assertFalse(obs)

    def test_historical_document_not_a_postrequest_deletion_failure(self):
        early = AT - timedelta(days=30)
        td, f = pdf_data(DOCUMENT, cap=early)
        with td:
            index = build_index([f], enhanced_readback=True, decode_sql_json=True, structured_attachment=True)
            plan = plan_case(request(), IDENTITY, index, RULES, variant="media-evidence-v5")
            self.assertEqual(plan.status, "COMPLETE")
            self.assertFalse(plan.steps)
            self.assertTrue(any("PRE_REQUEST_ONLY" in w for w in plan.warnings))

    def test_unrelated_legal_register_no_pii_state_inferred(self):
        text = "DISPUTED ORDERS AND TAX RECORDS REGISTER\nChargeback user_17 Retain until 2033\nStatus: Active"
        td, f = pdf_data(text)
        with td:
            obs, ok = scan_pdf(f, structured_attachment=True)
            self.assertTrue(ok)
            self.assertFalse(obs)

    def test_no_single_customer_email_never_guesses_owner(self):
        td, f = pdf_data(DOCUMENT.replace("priya.raman@gmail.com", "[REDACTED]"))
        with td:
            obs, ok = scan_pdf(f, structured_attachment=True)
            self.assertTrue(ok)
            self.assertFalse(obs)

    def test_explicit_deleted_attachment_is_not_present(self):
        td, f = pdf_data(DOCUMENT + "\nAttachment status: deleted")
        with td:
            obs, ok = scan_pdf(f, structured_attachment=True)
            self.assertTrue(ok)
            self.assertFalse(obs)


if __name__ == "__main__":
    unittest.main()
