# 🧾 ClearBill

**A private, on-device auditor for medical bills.** Drop in a provider bill and your insurer's
Explanation of Benefits (EOB). ClearBill reads both with an open vision model running locally in
Ollama, reconciles them line by line, explains every discrepancy in plain English, and drafts a
dispute letter. Your medical documents never leave your laptop.

> Informational only — not legal, medical or financial advice. Always review before sending anything.

## Why

Billing errors such as duplicate lines, insurance payments that never got posted, and balances
above what the EOB says you owe are common, and checking by hand is tedious. These documents are
also some of the most sensitive you own, which makes local inference the right default.

## How it works

```
PDF / photo ─► page images ─► gemma4 (vision, JSON-schema output) ─► Bill / EOB (Pydantic)
                                                                         │
                                         deterministic Python rules ◄────┘
                                                    │
                       plain-English summary + dispute letter (LLM, grounded & amount-checked)
```

**The LLM reads and writes; code counts.** Small models are great at reading messy documents and
bad at arithmetic, so every comparison and total is plain Python with unit tests. Any dollar
amount the model writes is checked against the audit facts; ungrounded output falls back to a template.

## Quick start (Apple Silicon, 16GB)

```bash
brew install --cask ollama-app && open -a Ollama
make models          # pulls gemma4 + qwen3:8b
make setup && source .venv/bin/activate
clearbill doctor     # checks Ollama and models
make test            # unit tests for the reconciliation engine
make synth           # 24 synthetic bill/EOB PDF pairs with injected errors
clearbill audit eval/cases/case_001_duplicate/bill.pdf eval/cases/case_001_duplicate/eob.pdf
make ui              # Streamlit app
```

Other commands:

```bash
clearbill extract my_bill.pdf --kind bill          # see what the model read
clearbill save-json bill.pdf eob.pdf --out-dir out # extract, then edit the JSON by hand
clearbill audit out/bill.json out/eob.json --json-input --no-llm
```

## Rules

| Code | Severity | Meaning |
|---|---|---|
| `DUPLICATE_CHARGE` | high | Same date + code billed more times than insurance processed |
| `LINE_BALANCE_EXCEEDS` | high | A line's balance is above the EOB's patient responsibility |
| `TOTAL_EXCEEDS_RESPONSIBILITY` | high | Amount due is above what the EOB says you owe |
| `INSURANCE_NOT_CREDITED` | medium | Bill shows less insurance payment than the EOB |
| `NO_EOB_MATCH` | medium | Charge never processed by insurance |
| `CHARGE_MISMATCH`, `EOB_LINE_NOT_ON_BILL` | info | Worth a look, usually benign |

## Evaluation

```bash
make eval-rules                                   # reconciler vs ground truth (no LLM)
make eval                                         # full extraction + audit with gemma4
python eval/run_eval.py --model qwen3:8b --limit 6
```

Reports per-field extraction accuracy, finding precision/recall and seconds per case. Use it to
compare models and prompts. Record your results here:

| Model | Field accuracy | Finding recall | s / case |
|---|---|---|---|
| gemma4 (12B) | | | |
| qwen3:8b | | | |

## Memory tips for 16GB Macs

- Keep `CLEARBILL_NUM_CTX` at 8192; pages are processed one at a time.
- Run one large model at a time (`ollama ps` to check, `ollama stop <model>` to unload).
- Close heavy apps during eval runs; watch Memory Pressure in Activity Monitor.

## Roadmap

See [`spec.md`](spec.md): SQLite audit history, plan-document RAG ("is this covered?"),
model A/B report, redaction mode.
