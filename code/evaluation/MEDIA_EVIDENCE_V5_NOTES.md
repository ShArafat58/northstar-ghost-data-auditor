# Step 8 — Media Evidence V5: controlled A/B experiment

## Hypothesis

Some rendered support attachment PDFs split a typed support resource reference,
customer identity, and attachment description across separate page lines. The
existing one-line parser misses the linkage. A narrowly scoped document parser
may recover **PRESENT SUPPORT_ATTACHMENT** evidence without inferring absent
records, broad co-occurrence links, or current state from old reference images.

## Implementation

- New `media-evidence-v5` baseline variant, still sample-only.
- Retains V4 SQL JSON-envelope decoding, V2 readback, and intent V2.
- Reads PDF page text in geometric (`sort=True`) order for V5 only.
- Accepts an attachment observation only when all gates hold:
  1. One **numbered `support_...` ID** on a line labeled `Support reference`, `Support case` or `Support ticket`.
  2. The PDF mentions an `attachment` and an explicit `Customer request`, `Customer details`, or `Customer information` section.
  3. Exactly one unique email address occurs in the PDF text.
  4. No explicit `Attachment status: deleted/removed/absent/redacted` claim.
- Emits only `SUPPORT_ATTACHMENT`/`PRESENT` with the *PDF's evidence ID* and
  the file's capture time; no absence or completed deletion is ever inferred.
- Rejects multi-subject documents; does not promote legal-retention registers,
  historical account-reference registry screenshots, or generic media text.
- The planner's existing post-request cutoff prevents a pre-request PDF from
  triggering a false remaining-record finding.
- No sample labels or reference output rows are imported by the patch.

## Evidence grounding

The case-07 diagnostic reported `evidence_58` as an unindexed PDF containing
`support_001`, an email and a phone. The observed PDF extraction had the
heading `DELIVERY ADDRESS CORRECTION REQUEST`, `Support reference CS-4827 /
support_001`, `Customer request`, and an attachment-properties section.
The case-17 historical PDF `evidence_27` and image `evidence_42` establish
context, not necessarily current state. V5 deliberately does not assert them
as post-request deletion evidence.

## A/B runs (same OCR environment)

```
python -m unittest discover -s .\code\tests -p "test_*.py" -v
python .\code\run_baseline.py --sample --variant embedded-sql-v4 --out .\code\evaluation\baseline_v4_v5_control.csv
python .\code\run_baseline.py --sample --variant media-evidence-v5 --out .\code\evaluation\baseline_media_evidence_v5.csv
python .\code\evaluation\main.py --predictions .\code\evaluation\baseline_v4_v5_control.csv --compare .\code\evaluation\baseline_media_evidence_v5.csv
python .\code\evaluation\main.py --predictions .\code\evaluation\baseline_media_evidence_v5.csv
```

Run commands **one at a time** in Antigravity PowerShell. Do not paste
multiline PowerShell here-strings; that triggered PSReadLine console crashes.

## Evaluation policy

The local test suite of 63 tests passed on synthetic data; this is **not** a
sample accuracy claim. Promote V5 only if actual side-by-side accuracy improves
without unacceptable regressions. V4 remains the current best until tested.
