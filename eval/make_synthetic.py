"""Generate synthetic bill/EOB PDF pairs with ground truth and injected errors.

    python eval/make_synthetic.py --n 20 --out eval/cases

Each case folder holds bill.pdf, eob.pdf, bill.json, eob.json and expected.json
(the finding codes the reconciler should raise). All names and numbers are fake.
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

SERVICES = [
    ("99213", "Office visit, est. patient", 180, 260),
    ("99214", "Office visit, moderate", 240, 340),
    ("80053", "Comprehensive metabolic panel", 60, 140),
    ("85025", "Complete blood count", 30, 90),
    ("36415", "Venipuncture", 15, 40),
    ("71046", "Chest X-ray, 2 views", 120, 300),
    ("93000", "Electrocardiogram", 70, 180),
    ("97110", "Therapeutic exercise", 90, 160),
]
PROVIDERS = ["Lakeside Family Medicine", "Bayview Imaging Center", "Pine Ridge Physical Therapy", "Harbor Labs"]
PAYERS = ["Northstar Health Plan", "Evergreen Mutual Insurance", "Summit Care PPO"]
ERRORS = ["none", "duplicate", "overbill_total", "not_credited", "unprocessed", "line_balance"]


def money(x: float) -> str:
    return f"${x:,.2f}"


def make_case(rng: random.Random, idx: int) -> tuple[dict, dict, list[str], str]:
    dos = date(2026, 1, 1) + timedelta(days=rng.randint(0, 200))
    services = rng.sample(SERVICES, rng.randint(2, 4))
    error = ERRORS[idx % len(ERRORS)]
    # A fully-deductible claim has $0 plan payment, so there is nothing to "not credit".
    deductible_left = 0 if error == "not_credited" else rng.choice([0, 0, 50, 150])
    coins_rate = rng.choice([0.1, 0.2, 0.3])
    eob_lines, bill_lines = [], []
    for code, desc, lo, hi in services:
        billed = float(rng.randint(lo, hi))
        allowed = round(billed * rng.uniform(0.35, 0.7), 2)
        ded = min(deductible_left, allowed)
        deductible_left -= ded
        coins = round((allowed - ded) * coins_rate, 2)
        resp = round(ded + coins, 2)
        paid = round(allowed - resp, 2)
        eob_lines.append(dict(date_of_service=dos.isoformat(), cpt_code=code, description=desc, billed=billed,
                              allowed=allowed, plan_paid=paid, deductible=ded, copay=0.0, coinsurance=coins,
                              not_covered=round(billed - allowed, 2), patient_responsibility=resp))
        bill_lines.append(dict(date_of_service=dos.isoformat(), cpt_code=code, description=desc, charge=billed,
                               patient_balance=None))

    resp_total = round(sum(l["patient_responsibility"] for l in eob_lines), 2)
    paid_total = round(sum(l["plan_paid"] for l in eob_lines), 2)
    adj_total = round(sum(l["not_covered"] for l in eob_lines), 2)
    bill = dict(provider_name=rng.choice(PROVIDERS), statement_date=(dos + timedelta(days=45)).isoformat(),
                account_number=f"A{rng.randint(100000, 999999)}", line_items=bill_lines,
                total_charges=round(sum(l["charge"] for l in bill_lines), 2), insurance_payments=paid_total,
                adjustments=adj_total, patient_payments=0.0, total_due=resp_total)
    eob = dict(payer_name=rng.choice(PAYERS), provider_name=bill["provider_name"],
               claim_number=f"CLM{rng.randint(10**8, 10**9 - 1)}", line_items=eob_lines,
               total_patient_responsibility=resp_total)

    expected: list[str] = []
    if error == "duplicate":
        dup = dict(bill_lines[0])
        bill_lines.append(dup)
        bill["total_charges"] += dup["charge"]
        bill["total_due"] = round(bill["total_due"] + dup["charge"], 2)
        expected = ["DUPLICATE_CHARGE", "TOTAL_EXCEEDS_RESPONSIBILITY"]
    elif error == "overbill_total":
        bill["total_due"] = round(bill["total_due"] + rng.choice([25, 60, 110]), 2)
        expected = ["TOTAL_EXCEEDS_RESPONSIBILITY"]
    elif error == "not_credited":
        short = round(max(l["plan_paid"] for l in eob_lines), 2)
        bill["insurance_payments"] = round(paid_total - short, 2)
        bill["total_due"] = round(bill["total_due"] + short, 2)
        expected = ["INSURANCE_NOT_CREDITED", "TOTAL_EXCEEDS_RESPONSIBILITY"]
    elif error == "unprocessed":
        code, desc, lo, hi = next(s for s in SERVICES if s[0] not in {l["cpt_code"] for l in bill_lines})
        charge = float(rng.randint(lo, hi))
        bill_lines.append(dict(date_of_service=dos.isoformat(), cpt_code=code, description=desc, charge=charge,
                               patient_balance=None))
        bill["total_charges"] += charge
        bill["total_due"] = round(bill["total_due"] + charge, 2)
        expected = ["NO_EOB_MATCH"]
    elif error == "line_balance":
        for bl, el in zip(bill_lines, eob_lines):
            bl["patient_balance"] = el["patient_responsibility"]
        bill_lines[0]["patient_balance"] = round(eob_lines[0]["patient_responsibility"] + 40, 2)
        bill["total_due"] = round(bill["total_due"] + 40, 2)
        expected = ["LINE_BALANCE_EXCEEDS", "TOTAL_EXCEEDS_RESPONSIBILITY"]
    bill["total_charges"] = round(bill["total_charges"], 2)
    return bill, eob, expected, error


def _table(c: canvas.Canvas, y: float, headers: list[str], rows: list[list[str]], xs: list[float]) -> float:
    c.setFont("Helvetica-Bold", 8)
    for x, h in zip(xs, headers):
        c.drawString(x, y, h)
    c.line(xs[0], y - 3, 7.9 * inch, y - 3)
    c.setFont("Helvetica", 8)
    for row in rows:
        y -= 14
        for x, v in zip(xs, row):
            c.drawString(x, y, v)
    return y


def render_bill(bill: dict, path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(0.6 * inch, 10.3 * inch, bill["provider_name"])
    c.setFont("Helvetica", 9)
    c.drawString(0.6 * inch, 10.05 * inch, "PATIENT STATEMENT")
    c.drawString(5.2 * inch, 10.3 * inch, f"Statement date: {date.fromisoformat(bill['statement_date']):%m/%d/%Y}")
    c.drawString(5.2 * inch, 10.1 * inch, f"Account #: {bill['account_number']}")
    xs = [x * inch for x in (0.6, 1.5, 2.2, 5.4, 6.6)]
    has_bal = any(l["patient_balance"] is not None for l in bill["line_items"])
    rows = [[f"{date.fromisoformat(l['date_of_service']):%m/%d/%Y}", l["cpt_code"], l["description"], money(l["charge"]),
             money(l["patient_balance"]) if l["patient_balance"] is not None else ""] for l in bill["line_items"]]
    y = _table(c, 9.4 * inch, ["Date", "Code", "Description", "Charges", "Your balance" if has_bal else ""], rows, xs)
    y -= 0.4 * inch
    c.setFont("Helvetica", 9)
    for label, key in [("Total charges", "total_charges"), ("Insurance payments", "insurance_payments"),
                       ("Adjustments", "adjustments"), ("Patient payments", "patient_payments")]:
        sign = "-" if key != "total_charges" else ""
        c.drawString(4.6 * inch, y, label)
        c.drawRightString(7.6 * inch, y, sign + money(bill[key]))
        y -= 14
    c.setFont("Helvetica-Bold", 11)
    c.drawString(4.6 * inch, y - 6, "AMOUNT DUE")
    c.drawRightString(7.6 * inch, y - 6, money(bill["total_due"]))
    c.save()


def render_eob(eob: dict, path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(0.6 * inch, 10.3 * inch, eob["payer_name"])
    c.setFont("Helvetica", 9)
    c.drawString(0.6 * inch, 10.05 * inch, "EXPLANATION OF BENEFITS — THIS IS NOT A BILL")
    c.drawString(0.6 * inch, 9.8 * inch, f"Provider: {eob['provider_name']}    Claim #: {eob['claim_number']}")
    xs = [x * inch for x in (0.4, 1.15, 1.7, 2.45, 3.15, 3.85, 4.55, 5.25, 5.95, 6.75)]
    headers = ["Date", "Code", "Billed", "Allowed", "Not cov.", "Deduct.", "Copay", "Coins.", "Plan paid", "You owe"]
    rows = [[f"{date.fromisoformat(l['date_of_service']):%m/%d/%y}", l["cpt_code"], money(l["billed"]),
             money(l["allowed"]), money(l["not_covered"]), money(l["deductible"]), money(l["copay"]),
             money(l["coinsurance"]), money(l["plan_paid"]), money(l["patient_responsibility"])]
            for l in eob["line_items"]]
    y = _table(c, 9.3 * inch, headers, rows, xs)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(4.2 * inch, y - 0.4 * inch, "Total you may owe the provider:")
    c.drawRightString(7.6 * inch, y - 0.4 * inch, money(eob["total_patient_responsibility"]))
    c.save()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", type=Path, default=Path("eval/cases"))
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    for i in range(args.n):
        bill, eob, expected, error = make_case(rng, i)
        d = args.out / f"case_{i:03d}_{error}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "bill.json").write_text(json.dumps(bill, indent=2))
        (d / "eob.json").write_text(json.dumps(eob, indent=2))
        (d / "expected.json").write_text(json.dumps(expected))
        render_bill(bill, d / "bill.pdf")
        render_eob(eob, d / "eob.pdf")
    print(f"Wrote {args.n} cases to {args.out}")


if __name__ == "__main__":
    main()
