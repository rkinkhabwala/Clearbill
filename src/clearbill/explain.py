"""LLM-written summary and dispute letter, grounded only in the AuditReport.

Guardrail: every dollar amount in the model's output must already appear in the facts we gave
it. On failure we regenerate once, then fall back to a deterministic template.
"""
from __future__ import annotations

import json
import re

from . import llm
from .config import settings
from .models import EOB, Bill
from .reconcile import AuditReport

_MONEY = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{2})?)")

SUMMARY_SYSTEM = (
    "You explain medical bill discrepancies to a patient in plain, calm English. "
    "Use ONLY the facts and dollar amounts in the JSON provided. Never compute new numbers. "
    "Do not give legal or medical advice. Keep it under 200 words."
)

LETTER_SYSTEM = (
    "You write short, polite, firm billing dispute letters from a patient to a provider's billing office. "
    "Use ONLY the facts and dollar amounts in the JSON provided. Never compute new numbers or cite laws. "
    "Request a corrected statement and a hold on collections while the review is pending. "
    "Use [brackets] for anything unknown, like the patient's name or address."
)


def _facts(report: AuditReport, bill: Bill, eob: EOB) -> dict:
    return {
        "provider": bill.provider_name or eob.provider_name,
        "payer": eob.payer_name,
        "account_number": bill.account_number,
        "claim_number": eob.claim_number,
        "statement_date": bill.statement_date.isoformat() if bill.statement_date else None,
        "bill_total_due": report.billed_total_due,
        "eob_expected_patient_total": report.expected_patient_total,
        "potential_overcharge": report.potential_overcharge,
        "findings": [f.model_dump(mode="json", exclude={"bill_line", "eob_line"}) for f in report.actionable],
    }


def _allowed_amounts(facts: dict) -> set[float]:
    found: set[float] = set()

    def walk(x):
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            found.add(round(float(x), 2))
        elif isinstance(x, str):
            for m in _MONEY.findall(x):
                found.add(round(float(m.replace(",", "")), 2))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(facts)
    return found


def verify_amounts(text: str, facts: dict) -> list[float]:
    """Return dollar amounts in `text` that are not grounded in `facts` (empty list = OK)."""
    allowed = _allowed_amounts(facts)
    used = {round(float(m.replace(",", "")), 2) for m in _MONEY.findall(text)}
    return sorted(used - allowed)


def _grounded(system: str, prompt: str, facts: dict, fallback: str, model: str | None) -> str:
    model = model or settings.text_model
    for _ in range(2):
        out = llm.text(model, prompt, system=system)
        if not verify_amounts(out, facts):
            return out
    return fallback


def summarize(report: AuditReport, bill: Bill, eob: EOB, model: str | None = None) -> str:
    if not report.actionable:
        return "Good news: this bill matches your EOB. No discrepancies were found."
    facts = _facts(report, bill, eob)
    prompt = f"Explain these findings to the patient, most important first.\n\nFACTS:\n{json.dumps(facts, indent=2)}"
    return _grounded(SUMMARY_SYSTEM, prompt, facts, report.to_markdown(), model)


def template_letter(report: AuditReport, bill: Bill, eob: EOB) -> str:
    provider = bill.provider_name or eob.provider_name or "[Provider name]"
    bullets = "\n".join(f"- {f.title}: {f.detail}" for f in report.actionable)
    return f"""[Your name]
[Your address]
[Date]

{provider} — Billing Department

Re: Account {bill.account_number or '[account number]'}, claim {eob.claim_number or '[claim number]'}

To whom it may concern,

I have compared my statement with the Explanation of Benefits from {eob.payer_name or 'my insurer'} and found the following discrepancies:

{bullets}

Please review these items, send me a corrected statement, and place my account on hold from collections while the review is pending.

Thank you,
[Your name]
[Phone]
"""


def dispute_letter(report: AuditReport, bill: Bill, eob: EOB, model: str | None = None) -> str | None:
    if not report.actionable:
        return None
    facts = _facts(report, bill, eob)
    prompt = f"Write the dispute letter.\n\nFACTS:\n{json.dumps(facts, indent=2)}"
    return _grounded(LETTER_SYSTEM, prompt, facts, template_letter(report, bill, eob), model)
