"""Pure, deterministic bill ↔ EOB reconciliation. No I/O, no LLM.

See spec.md §6 for the rule table. Every rule skips silently when its inputs are missing.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field

from .config import settings
from .models import EOB, Bill, BillLineItem, EOBLineItem

TOL = settings.tolerance


class Severity(str, Enum):
    high = "high"
    medium = "medium"
    info = "info"


class Finding(BaseModel):
    code: str
    severity: Severity
    title: str
    detail: str
    amount: float | None = None
    bill_line: int | None = None
    eob_line: int | None = None


class AuditReport(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    matched: list[tuple[int, int]] = Field(default_factory=list)
    expected_patient_total: float | None = None
    billed_total_due: float | None = None
    potential_overcharge: float = 0.0

    @property
    def actionable(self) -> list[Finding]:
        return [f for f in self.findings if f.severity != Severity.info]

    def to_markdown(self) -> str:
        lines = ["# ClearBill audit", ""]
        if self.billed_total_due is not None:
            lines.append(f"- Bill says you owe: **${self.billed_total_due:,.2f}**")
        if self.expected_patient_total is not None:
            lines.append(f"- EOB says you owe (after your payments): **${self.expected_patient_total:,.2f}**")
        lines.append(f"- Potential overcharge: **${self.potential_overcharge:,.2f}**")
        lines.append(f"- Matched lines: {len(self.matched)}")
        lines.append("")
        if not self.findings:
            lines.append("No discrepancies found. The bill is consistent with the EOB.")
        order = {Severity.high: 0, Severity.medium: 1, Severity.info: 2}
        for f in sorted(self.findings, key=lambda f: order[f.severity]):
            amt = f" — ${f.amount:,.2f}" if f.amount is not None else ""
            lines.append(f"## [{f.severity.value.upper()}] {f.title}{amt}")
            lines.append(f.detail)
            lines.append("")
        return "\n".join(lines)


def _eq(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= TOL


def _fmt_line(line: BillLineItem | EOBLineItem) -> str:
    d = line.date_of_service.isoformat() if line.date_of_service else "unknown date"
    code = line.cpt_code or "no code"
    desc = f" ({line.description})" if line.description else ""
    return f"{d}, {code}{desc}"


def _key(d: date | None, code: str | None) -> tuple[date | None, str | None]:
    return (d, code)


def match_lines(bill: Bill, eob: EOB) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Greedy matching. Returns (pairs, unmatched_bill_idx, unmatched_eob_idx)."""
    remaining = list(range(len(eob.line_items)))
    pairs: list[tuple[int, int]] = []
    unmatched_bill: list[int] = []

    for i, bl in enumerate(bill.line_items):
        j = None
        if bl.cpt_code:
            j = next(
                (k for k in remaining
                 if eob.line_items[k].cpt_code == bl.cpt_code
                 and eob.line_items[k].date_of_service == bl.date_of_service),
                None,
            )
        if j is None:
            j = next(
                (k for k in remaining
                 if eob.line_items[k].date_of_service == bl.date_of_service
                 and _eq(eob.line_items[k].billed, bl.charge)
                 and not (bl.cpt_code and eob.line_items[k].cpt_code)),  # both coded + different = not a match
                None,
            )
        if j is None:
            unmatched_bill.append(i)
        else:
            pairs.append((i, j))
            remaining.remove(j)
    return pairs, unmatched_bill, remaining


def audit(bill: Bill, eob: EOB) -> AuditReport:
    findings: list[Finding] = []
    pairs, unmatched_bill, unmatched_eob = match_lines(bill, eob)

    # --- line-level rules -------------------------------------------------
    key_counts = Counter(_key(l.date_of_service, l.cpt_code) for l in bill.line_items if l.cpt_code)
    for i in unmatched_bill:
        bl = bill.line_items[i]
        if bl.cpt_code and key_counts[_key(bl.date_of_service, bl.cpt_code)] > 1:
            findings.append(Finding(
                code="DUPLICATE_CHARGE", severity=Severity.high,
                title="Possible duplicate charge",
                detail=f"The bill lists {_fmt_line(bl)} more than once, but the EOB processed it fewer times. "
                       "Ask the provider to remove the duplicate.",
                amount=bl.patient_balance if bl.patient_balance is not None else bl.charge,
                bill_line=i,
            ))
        else:
            findings.append(Finding(
                code="NO_EOB_MATCH", severity=Severity.medium,
                title="Charge not found on the EOB",
                detail=f"{_fmt_line(bl)} appears on the bill but not on this EOB. It may not have been "
                       "submitted to insurance yet, or it belongs to a different claim. Ask the provider to "
                       "bill insurance before you pay for it.",
                amount=bl.charge, bill_line=i,
            ))

    for i, j in pairs:
        bl, el = bill.line_items[i], eob.line_items[j]
        if (bl.patient_balance is not None and el.patient_responsibility is not None
                and bl.patient_balance > el.patient_responsibility + TOL):
            diff = round(bl.patient_balance - el.patient_responsibility, 2)
            findings.append(Finding(
                code="LINE_BALANCE_EXCEEDS", severity=Severity.high,
                title="Line balance is higher than your EOB responsibility",
                detail=f"For {_fmt_line(bl)} the bill asks ${bl.patient_balance:,.2f}, but the EOB says your "
                       f"responsibility is ${el.patient_responsibility:,.2f}.",
                amount=diff, bill_line=i, eob_line=j,
            ))
        if bl.charge is not None and el.billed is not None and not _eq(bl.charge, el.billed):
            findings.append(Finding(
                code="CHARGE_MISMATCH", severity=Severity.info,
                title="Charge differs from what insurance was billed",
                detail=f"For {_fmt_line(bl)} the bill shows ${bl.charge:,.2f}; the EOB shows "
                       f"${el.billed:,.2f} billed to insurance.",
                bill_line=i, eob_line=j,
            ))

    for j in unmatched_eob:
        findings.append(Finding(
            code="EOB_LINE_NOT_ON_BILL", severity=Severity.info,
            title="EOB service not on this bill",
            detail=f"{_fmt_line(eob.line_items[j])} is on the EOB but not on this bill. "
                   "It may be billed separately (e.g. by a lab or another provider).",
            eob_line=j,
        ))

    # --- totals ------------------------------------------------------------
    eob_resp = eob.total_patient_responsibility
    if eob_resp is None:
        vals = [l.patient_responsibility for l in eob.line_items]
        eob_resp = round(sum(vals), 2) if vals and all(v is not None for v in vals) else None

    expected = None
    if eob_resp is not None:
        expected = round(max(eob_resp - (bill.patient_payments or 0.0), 0.0), 2)
        # Unprocessed bill lines legitimately add to what's due; don't double-flag them.
        unprocessed = sum(bill.line_items[i].charge or 0.0 for i in unmatched_bill
                          if not any(f.code == "DUPLICATE_CHARGE" and f.bill_line == i for f in findings))
        if bill.total_due is not None and bill.total_due > expected + unprocessed + TOL:
            diff = round(bill.total_due - expected - unprocessed, 2)
            findings.append(Finding(
                code="TOTAL_EXCEEDS_RESPONSIBILITY", severity=Severity.high,
                title="Amount due is higher than the EOB says you owe",
                detail=f"The bill asks for ${bill.total_due:,.2f}. The EOB puts your responsibility at "
                       f"${eob_resp:,.2f}"
                       + (f", minus ${bill.patient_payments:,.2f} you already paid" if bill.patient_payments else "")
                       + f", so you should owe about ${expected:,.2f}"
                       + (f" plus ${unprocessed:,.2f} not yet processed by insurance" if unprocessed else "")
                       + ".",
                amount=diff,
            ))

    # Only EOB lines that appear on this bill — other lines may be billed by a different provider.
    plan_paid_vals = [eob.line_items[j].plan_paid for _, j in pairs if eob.line_items[j].plan_paid is not None]
    if bill.insurance_payments is not None and plan_paid_vals:
        plan_paid = round(sum(plan_paid_vals), 2)
        if bill.insurance_payments + TOL < plan_paid:
            findings.append(Finding(
                code="INSURANCE_NOT_CREDITED", severity=Severity.medium,
                title="Insurance payment may not be fully credited",
                detail=f"The EOB says your plan paid ${plan_paid:,.2f}, but the bill only shows "
                       f"${bill.insurance_payments:,.2f} in insurance payments. The payment may still be "
                       "posting — ask the provider to confirm before paying.",
                amount=round(plan_paid - bill.insurance_payments, 2),
            ))

    # --- headline number (no double counting) -------------------------------
    total_flag = next((f for f in findings if f.code == "TOTAL_EXCEEDS_RESPONSIBILITY"), None)
    if total_flag:
        overcharge = total_flag.amount or 0.0
    else:
        overcharge = sum(f.amount or 0.0 for f in findings
                         if f.severity == Severity.high and f.bill_line is not None)

    return AuditReport(
        findings=findings, matched=pairs, expected_patient_total=expected,
        billed_total_due=bill.total_due, potential_overcharge=round(overcharge, 2),
    )
