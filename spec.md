# ClearBill — Local Medical Bill & EOB Auditor

## 1. Problem

US patients routinely receive provider bills that don't match what their insurer says they owe.
Common errors: duplicate line items, insurance payments not yet credited, balances above the
EOB's "patient responsibility", and charges for services insurance never processed. Checking a
bill against an Explanation of Benefits (EOB) by hand is tedious, so most people just pay.

ClearBill reads a provider bill and the matching EOB, reconciles them line by line, explains every
discrepancy in plain English, and drafts a dispute letter — **entirely on the user's laptop**.

## 2. Goals / Non-goals

**Goals**
- 100% local inference via Ollama. No network calls except `localhost:11434`.
- Accept PDFs and phone photos of bills/EOBs.
- Deterministic, testable reconciliation — the LLM never does arithmetic.
- Every number in an explanation or letter traces back to an extracted field.
- Measurable extraction quality via a synthetic eval set.

**Non-goals (v1)**
- Legal or medical advice. Output is informational; the letter is a draft for the user to review.
- Insurer/provider portal integrations.
- Coding-level audits (e.g. upcoding, NCCI edits). Possible v2.

## 3. Hardware budget (MacBook Pro M1 Pro, 16GB unified memory)

| Component | Model | Approx. memory |
|---|---|---|
| Vision extraction + text | `gemma4` (12B, Q4) | ~7 GB |
| Fallback / A-B text model | `qwen3:8b` or `llama3.1:8b` | ~5 GB |
| Embeddings (phase 4) | `nomic-embed-text` | <0.5 GB |

Rules: one large model loaded at a time; `num_ctx` = 8192; `temperature` = 0 for extraction;
process multi-page documents one page at a time and merge.

## 4. Architecture

```
bill.pdf / eob.jpg
   │  pypdfium2 / Pillow  → page PNGs (150 dpi, max 1600px)
   ▼
[extract.py]  vision LLM + JSON schema (Ollama `format=`) → per-page Bill / EOB → merge
   ▼
[models.py]   Pydantic validation; normalizes dates ("03/14/26") and money ("$1,204.00", "(12.00)")
   ▼
[reconcile.py] pure Python: match lines → apply rules → AuditReport
   ▼
[explain.py]  text LLM: plain-English summary + dispute letter, fed ONLY the report JSON
   ▼
CLI (typer)  /  Streamlit UI  /  (phase 2) SQLite audit history
```

Design principle: **the LLM reads and writes; code counts.**

## 5. Data model

`Bill`: provider_name, statement_date, account_number, line_items[], total_charges,
insurance_payments, adjustments, patient_payments, total_due.
`BillLineItem`: date_of_service, cpt_code, description, charge, patient_balance.

`EOB`: payer_name, provider_name, claim_number, line_items[], total_patient_responsibility.
`EOBLineItem`: date_of_service, cpt_code, description, billed, allowed, plan_paid,
deductible, copay, coinsurance, not_covered, patient_responsibility.

All fields optional (nullable) so partial pages validate; reconciliation skips rules whose inputs
are missing.

## 6. Matching & rules

Line matching (greedy, each EOB line used once):
1. same date_of_service AND same CPT code
2. else same date_of_service AND same charge == billed (±$0.01)

| Code | Severity | Fires when |
|---|---|---|
| `DUPLICATE_CHARGE` | high | Unmatched bill line shares date+CPT with another bill line |
| `NO_EOB_MATCH` | medium | Bill line has no EOB line (claim may not be processed yet) |
| `LINE_BALANCE_EXCEEDS` | high | Bill line patient_balance > EOB patient_responsibility |
| `TOTAL_EXCEEDS_RESPONSIBILITY` | high | total_due > EOB patient responsibility − patient_payments |
| `INSURANCE_NOT_CREDITED` | medium | Bill insurance_payments < EOB total plan_paid |
| `CHARGE_MISMATCH` | info | Bill charge ≠ EOB billed for a matched line |
| `EOB_LINE_NOT_ON_BILL` | info | EOB line has no bill line |

`potential_overcharge` = the `TOTAL_EXCEEDS_RESPONSIBILITY` amount if present, else the sum of
high-severity line amounts (avoids double counting).

## 7. Phases & acceptance criteria

**Phase 1 — Extraction + reconciliation (scaffolded)**
- `clearbill extract <file> --kind bill|eob` prints valid JSON.
- `clearbill audit bill eob` prints a Markdown report.
- `pytest` green; every rule has at least one positive and one negative test.

**Phase 2 — Explanations, letters, persistence**
- Summary + dispute letter generated from report JSON only.
- Guardrail: post-check that every dollar amount in LLM output appears in the report
  (`explain.verify_amounts`); regenerate once on failure, else fall back to the template.
- SQLite audit history (`~/.clearbill/audits.db`).

**Phase 3 — UI + eval**
- Streamlit app: upload, review/edit extracted JSON, run audit, download letter.
- `eval/run_eval.py` reports line-item field accuracy and finding recall on ≥20 synthetic cases.
- Targets: ≥90% field accuracy on clean PDFs, ≥75% on photos; ≥95% finding recall given
  correct extraction.

**Phase 4 — Plan-document RAG**
- Ingest Summary of Benefits PDF → chunks → `nomic-embed-text` → `sqlite-vec`.
- "Is this covered?" Q&A with page citations.

**Phase 5 — Polish**
- Model A/B report (gemma4 vs qwen3:8b: accuracy, latency, memory).
- Redaction mode for screenshots in README; packaged app (e.g. `pipx install`).

## 8. Eval

`eval/make_synthetic.py` renders randomized bill/EOB PDFs from ground-truth JSON with injected
errors and records the expected finding codes. `eval/run_eval.py` extracts each case with the
configured model and reports:
- field accuracy per line-item field (exact for codes/dates, ±$0.01 for money)
- finding precision/recall vs expected codes
- seconds per page

## 9. Repo layout

```
src/clearbill/  config.py models.py llm.py extract.py reconcile.py explain.py cli.py app.py
tests/          test_models.py test_reconcile.py
eval/           make_synthetic.py run_eval.py
```

## 10. Conventions (for Claude Code)

- Python 3.11+, Pydantic v2, type hints everywhere, no network libraries besides `ollama`.
- Reconciliation stays pure (no I/O, no LLM) and fully unit-tested.
- New rules: add to the table in §6, implement in `reconcile.py`, add tests.
- Prompts live next to the function that uses them, as module constants.
- Never log raw document contents; logs contain counts and timings only.
