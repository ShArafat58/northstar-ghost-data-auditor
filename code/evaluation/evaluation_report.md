# Evaluation Report

## Dataset and provenance

- Labeled reference: `dataset/sample_output.csv` (32 cases).
- Prediction input: `baseline_v6.csv`.
- Labels are read only by this evaluator. No inference component loads the reference file.
- All reported results are computed from the actual input predictions; no estimates are inserted.

## Core metrics

| Metric | Observed |
|---|---:|
| Prediction coverage | 32/32 (100.0%) |
| Contract-valid predicted rows | 32/32 (100.0%) |
| Case-level status accuracy (missing=incorrect) | 32/32 (100.0%) |
| Exact step precision, order-independent | 160/160 (100.0%) |
| Exact step recall, order-independent | 160/193 (82.9%) |
| Exact step F1 | 90.7% |
| Whole-case sequence accuracy | 10/32 (31.2%) |
| Exact full-row accuracy | 10/32 (31.2%) |

**Definitions:** Exact steps match on phase, system_id, resource_id, operation, state, and evidence_id using multiset matching. Sequence requires the entire ordered step array, including consecutive step_no, to match. Field accuracy compares corresponding ordinal steps; missing and extra steps contribute incorrect fields. Every missing case is wrong, not excluded from the denominator.

## Field-level accuracy

| Field | Exact positional accuracy |
|---|---:|
| `step_no` | 160/193 (82.9%) |
| `phase` | 160/193 (82.9%) |
| `system_id` | 119/193 (61.7%) |
| `resource_id` | 119/193 (61.7%) |
| `operation` | 129/193 (66.8%) |
| `state` | 129/193 (66.8%) |
| `evidence_id` | 160/193 (82.9%) |

## Status precision and recall

| Status | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| `COMPLETE` | 10/10 (100.0%) | 10/10 (100.0%) | 100.0% | 10 |
| `INCOMPLETE` | 22/22 (100.0%) | 22/22 (100.0%) | 100.0% | 22 |
| `NOT_APPLICABLE` | N/A | N/A | N/A | 0 |

## Status confusion pairs

| Expected status | Predicted status | Cases |
|---|---|---:|
| `COMPLETE` | `COMPLETE` | 10 |
| `INCOMPLETE` | `INCOMPLETE` | 22 |

## Step operation precision and recall

| Operation | Exact-resource step precision | Exact-resource step recall |
|---|---:|---:|
| `ANONYMIZE_RECORD` | 5/5 (100.0%) | 5/5 (100.0%) |
| `BLOCK_RESTORE` | 3/3 (100.0%) | 3/3 (100.0%) |
| `DELETE_RECORD` | 21/21 (100.0%) | 21/21 (100.0%) |
| `DISABLE_DATA_FLOW` | 6/6 (100.0%) | 6/6 (100.0%) |
| `REDACT_RECORD` | 6/6 (100.0%) | 6/6 (100.0%) |
| `REPORT_ACTIVE_DATA_FLOW` | 6/6 (100.0%) | 6/6 (100.0%) |
| `REPORT_EXPOSED_PII` | 11/11 (100.0%) | 11/11 (100.0%) |
| `REPORT_REMAINING_RECORD` | 21/21 (100.0%) | 21/21 (100.0%) |
| `REPORT_RESTORE_RISK` | 3/3 (100.0%) | 3/3 (100.0%) |
| `VERIFY_DATA_FLOW_DISABLED` | 9/9 (100.0%) | 9/9 (100.0%) |
| `VERIFY_RECORD_ABSENT` | 31/31 (100.0%) | 31/50 (62.0%) |
| `VERIFY_RECORD_ANONYMIZED` | 5/5 (100.0%) | 5/12 (41.7%) |
| `VERIFY_RECORD_PRESENT` | 17/17 (100.0%) | 17/24 (70.8%) |
| `VERIFY_RECORD_REDACTED` | 10/10 (100.0%) | 10/10 (100.0%) |
| `VERIFY_RESTORE_BLOCKED` | 6/6 (100.0%) | 6/6 (100.0%) |

## Custom synthetic evaluation cases

| Case | Scenario | Result |
|---|---|---|
| `custom_no_deletion` | No deletion authorization means NOT_APPLICABLE with no steps. | PASS |
| `custom_remaining_profile` | A verified remaining profile must be reported, removed, then checked. | PASS |
| `custom_retention_preserved` | A policy-required retained audit record must remain with cited proof. | PASS |

These three additional cases validate the evaluator and output-contract handling. They do not establish end-to-end agent performance on unseen evidence.

## Failures and diagnostic findings

- Cases with any mismatch or invalid output: 22.
- Missing predictions: 0.
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
| `case_07` | `INCOMPLETE` | `INCOMPLETE` | 3 | 4/3 | value/order mismatch |
| `case_08` | `INCOMPLETE` | `INCOMPLETE` | 3 | 4/3 | value/order mismatch |
| `case_09` | `INCOMPLETE` | `INCOMPLETE` | 4 | 5/4 | value/order mismatch |
| `case_10` | `INCOMPLETE` | `INCOMPLETE` | 3 | 4/3 | value/order mismatch |
| `case_18` | `INCOMPLETE` | `INCOMPLETE` | 12 | 14/12 | value/order mismatch |
| `case_20` | `INCOMPLETE` | `INCOMPLETE` | 6 | 8/6 | value/order mismatch |
| `case_21` | `COMPLETE` | `COMPLETE` | 1 | 3/1 | value/order mismatch |
| `case_22` | `INCOMPLETE` | `INCOMPLETE` | 7 | 8/7 | value/order mismatch |
| `case_23` | `INCOMPLETE` | `INCOMPLETE` | 6 | 8/6 | value/order mismatch |
| `case_24` | `INCOMPLETE` | `INCOMPLETE` | 6 | 7/6 | value/order mismatch |
| `case_25` | `COMPLETE` | `COMPLETE` | 1 | 3/1 | value/order mismatch |
| `case_26` | `COMPLETE` | `COMPLETE` | 4 | 6/4 | value/order mismatch |
| `case_27` | `COMPLETE` | `COMPLETE` | 4 | 6/4 | value/order mismatch |
| `case_28` | `COMPLETE` | `COMPLETE` | 4 | 6/4 | value/order mismatch |
| `case_29` | `INCOMPLETE` | `INCOMPLETE` | 12 | 14/12 | value/order mismatch |
| `case_30` | `INCOMPLETE` | `INCOMPLETE` | 12 | 14/12 | value/order mismatch |
| `case_31` | `COMPLETE` | `COMPLETE` | 2 | 3/2 | value/order mismatch |
| `case_32` | `INCOMPLETE` | `INCOMPLETE` | 4 | 5/4 | value/order mismatch |

## Side-by-side A/B comparison

A: `baseline_v6.csv`. B: `step9_agent32.csv`.

| Metric | A | B | Change (B-A) |
|---|---:|---:|---:|
| Coverage | 32 | 32 | +0 |
| Status correct | 32 | 32 | +0 |
| Exact step hits | 160 | 160 | +0 |
| Exact sequences | 10 | 10 | +0 |
| Exact rows | 10 | 10 | +0 |
| Exact field positions | 976 | 976 | +0 |

Fixed exact cases: None

Regressed exact cases: None

