# Step 9 Agent Usage Report

This report describes **real execution measurements**, not simulated or estimated token counts.

- Scope: 32-case sample/smoke
- Output: `step9_agent_smoke.csv`
- Completion: all selected cases
- Requested mode: `agent`
- Model: `openai/gpt-oss-20b`
- Provider: **GroqCloud** for model calls; deterministic V6 is the fallback (no model tokens).
- Requests covered: **5**
- Cases with accepted model tool submission: **0**
- Cases using V6 baseline/fallback: **5**
- Cases attempted with model: **0**
- Model completions: **0**
- Prompt/input tokens: **0**
- Completion/output tokens: **0**
- Total tokens: **0**
- Avg input tokens per selected request: **0.00**
- Avg output tokens per selected request: **0.00**
- Avg total tokens per selected request: **0.00**
- Avg model calls per selected request: **0.000**
- Rejected tool submissions: **0**
- Fallback count: **5** (reasons: ProviderError=5)
- Local tool calls: none

## Estimated Cost

- List-price input: **$0.075 per million tokens**
- List-price output: **$0.300 per million tokens**
- Estimated total usage at list pricing: **$0.000000**
- Estimated usage per selected request: **$0.00000000**
- Price source: https://console.groq.com/docs/model/openai/gpt-oss-20b (verify current pricing before submission).
- Actual Groq billing/free-tier charges may differ. Unknown model pricing is reported with zero configured price, not a guarantee of free inference.
- Model calls and token counts come from provider `usage`; retry attempts that never returned a usable response may be undercounted.

## Runtime Audit

A model decision counts only if it performed local function-tool calls and its
`submit_audit` was accepted by strict policy/evidence validation. The V6 fallback
is labeled distinctly and does **not** count as agent inference. All plans pass
schema validation; output IDs/ordering and checkpoint fingerprints are checked.
No prompts, account identifiers, API responses, or credentials are stored here.
This is a usage report, not evidence of hidden-label accuracy.
