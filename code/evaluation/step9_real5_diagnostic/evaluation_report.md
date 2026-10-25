# Evaluation Report

## Dataset and provenance

- Labeled reference: `dataset/sample_output.csv` (32 cases).
- Prediction input: `step9_real5.csv`.
- Labels are read only by this evaluator. No inference component loads the reference file.
- All reported results are computed from the actual input predictions; no estimates are inserted.

## Core metrics

| Metric | Observed |
|---|---:|
| Prediction coverage | 5/32 (15.6%) |
| Contract-valid predicted rows | 5/5 (100.0%) |
| Case-level status accuracy (missing=incorrect) | 5/32 (15.6%) |
| Exact step precision, order-independent | 5/5 (100.0%) |
| Exact step recall, order-independent | 5/193 (2.6%) |
| Exact step F1 | 5.1% |
| Whole-case sequence accuracy | 1/32 (3.1%) |
| Exact full-row accuracy | 1/32 (3.1%) |

**Definitions:** Exact steps match on phase, system_id, resource_id, operation, state, and evidence_id using multiset matching. Sequence requires the entire ordered step array, including consecutive step_no, to match. Field accuracy compares corresponding ordinal steps; missing and extra steps contribute incorrect fields. Every missing case is wrong, not excluded from the denominator.

**Coverage gate: NOT A FULL-BASELINE SCORE.** Some labelled cases have no predictions. This is only a partial diagnostic run; produce predictions for all sample cases before using accuracy to select a model or ship.

## Field-level accuracy

| Field | Exact positional accuracy |
|---|---:|
| `step_no` | 5/193 (2.6%) |
| `phase` | 5/193 (2.6%) |
| `system_id` | 5/193 (2.6%) |
| `resource_id` | 5/193 (2.6%) |
| `operation` | 5/193 (2.6%) |
| `state` | 5/193 (2.6%) |
| `evidence_id` | 5/193 (2.6%) |

## Status precision and recall

| Status | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| `COMPLETE` | 4/4 (100.0%) | 4/10 (40.0%) | 57.1% | 10 |
| `INCOMPLETE` | 1/1 (100.0%) | 1/22 (4.5%) | 8.7% | 22 |
| `NOT_APPLICABLE` | N/A | N/A | N/A | 0 |

## Status confusion pairs

| Expected status | Predicted status | Cases |
|---|---|---:|
| `COMPLETE` | `<MISSING>` | 6 |
| `COMPLETE` | `COMPLETE` | 4 |
| `INCOMPLETE` | `<MISSING>` | 21 |
| `INCOMPLETE` | `INCOMPLETE` | 1 |

## Step operation precision and recall

| Operation | Exact-resource step precision | Exact-resource step recall |
|---|---:|---:|
| `ANONYMIZE_RECORD` | N/A | 0/5 (0.0%) |
| `BLOCK_RESTORE` | N/A | 0/3 (0.0%) |
| `DELETE_RECORD` | 1/1 (100.0%) | 1/21 (4.8%) |
| `DISABLE_DATA_FLOW` | N/A | 0/6 (0.0%) |
| `REDACT_RECORD` | N/A | 0/6 (0.0%) |
| `REPORT_ACTIVE_DATA_FLOW` | N/A | 0/6 (0.0%) |
| `REPORT_EXPOSED_PII` | N/A | 0/11 (0.0%) |
| `REPORT_REMAINING_RECORD` | 1/1 (100.0%) | 1/21 (4.8%) |
| `REPORT_RESTORE_RISK` | N/A | 0/3 (0.0%) |
| `VERIFY_DATA_FLOW_DISABLED` | N/A | 0/9 (0.0%) |
| `VERIFY_RECORD_ABSENT` | 2/2 (100.0%) | 2/50 (4.0%) |
| `VERIFY_RECORD_ANONYMIZED` | N/A | 0/12 (0.0%) |
| `VERIFY_RECORD_PRESENT` | 1/1 (100.0%) | 1/24 (4.2%) |
| `VERIFY_RECORD_REDACTED` | N/A | 0/10 (0.0%) |
| `VERIFY_RESTORE_BLOCKED` | N/A | 0/6 (0.0%) |

## Custom synthetic evaluation cases

| Case | Scenario | Result |
|---|---|---|
| `custom_no_deletion` | No deletion authorization means NOT_APPLICABLE with no steps. | PASS |
| `custom_remaining_profile` | A verified remaining profile must be reported, removed, then checked. | PASS |
| `custom_retention_preserved` | A policy-required retained audit record must remain with cited proof. | PASS |

These three additional cases validate the evaluator and output-contract handling. They do not establish end-to-end agent performance on unseen evidence.

## Failures and diagnostic findings

- Cases with any mismatch or invalid output: 31.
- Missing predictions: 27.
- Predictions with unknown case IDs: 0.
- Each error is listed below and written to `mismatches.jsonl` with per-position expected/predicted objects.
- Improvement implemented in Step 7: strict CSV/schema checks, per-field scoring, operation-level error diagnostics, missing-case penalties and an A/B evaluation interface.
- Next improvement is conditional on measured complete-sample errors. No agent accuracy improvement is claimed yet.

### Complete mismatch index

| Case | Expected | Predicted | Matching steps | Expected/predicted steps | Error |
|---|---|---|---:|---:|---|
| `case_01` | `COMPLETE` | `COMPLETE` | 0 | 1/0 | value/order mismatch |
| `case_02` | `COMPLETE` | `COMPLETE` | 0 | 1/0 | value/order mismatch |
| `case_03` | `COMPLETE` | `COMPLETE` | 2 | 3/2 | value/order mismatch |
| `case_04` | `COMPLETE` | `COMPLETE` | 0 | 2/0 | value/order mismatch |
| `case_06` | `INCOMPLETE` | `<MISSING>` | 0 | 4/0 | Missing prediction |
| `case_07` | `INCOMPLETE` | `<MISSING>` | 0 | 4/0 | Missing prediction |
| `case_08` | `INCOMPLETE` | `<MISSING>` | 0 | 4/0 | Missing prediction |
| `case_09` | `INCOMPLETE` | `<MISSING>` | 0 | 5/0 | Missing prediction |
| `case_10` | `INCOMPLETE` | `<MISSING>` | 0 | 4/0 | Missing prediction |
| `case_11` | `INCOMPLETE` | `<MISSING>` | 0 | 14/0 | Missing prediction |
| `case_12` | `INCOMPLETE` | `<MISSING>` | 0 | 5/0 | Missing prediction |
| `case_13` | `INCOMPLETE` | `<MISSING>` | 0 | 8/0 | Missing prediction |
| `case_14` | `INCOMPLETE` | `<MISSING>` | 0 | 5/0 | Missing prediction |
| `case_15` | `INCOMPLETE` | `<MISSING>` | 0 | 5/0 | Missing prediction |
| `case_16` | `INCOMPLETE` | `<MISSING>` | 0 | 7/0 | Missing prediction |
| `case_17` | `INCOMPLETE` | `<MISSING>` | 0 | 5/0 | Missing prediction |
| `case_18` | `INCOMPLETE` | `<MISSING>` | 0 | 14/0 | Missing prediction |
| `case_19` | `INCOMPLETE` | `<MISSING>` | 0 | 8/0 | Missing prediction |
| `case_20` | `INCOMPLETE` | `<MISSING>` | 0 | 8/0 | Missing prediction |
| `case_21` | `COMPLETE` | `<MISSING>` | 0 | 3/0 | Missing prediction |
| `case_22` | `INCOMPLETE` | `<MISSING>` | 0 | 8/0 | Missing prediction |
| `case_23` | `INCOMPLETE` | `<MISSING>` | 0 | 8/0 | Missing prediction |
| `case_24` | `INCOMPLETE` | `<MISSING>` | 0 | 7/0 | Missing prediction |
| `case_25` | `COMPLETE` | `<MISSING>` | 0 | 3/0 | Missing prediction |
| `case_26` | `COMPLETE` | `<MISSING>` | 0 | 6/0 | Missing prediction |
| `case_27` | `COMPLETE` | `<MISSING>` | 0 | 6/0 | Missing prediction |
| `case_28` | `COMPLETE` | `<MISSING>` | 0 | 6/0 | Missing prediction |
| `case_29` | `INCOMPLETE` | `<MISSING>` | 0 | 14/0 | Missing prediction |
| `case_30` | `INCOMPLETE` | `<MISSING>` | 0 | 14/0 | Missing prediction |
| `case_31` | `COMPLETE` | `<MISSING>` | 0 | 3/0 | Missing prediction |
| `case_32` | `INCOMPLETE` | `<MISSING>` | 0 | 5/0 | Missing prediction |
