from datetime import date

from clearbill.models import EOB, Bill, BillLineItem, EOBLineItem
from clearbill.reconcile import audit

D1, D2 = date(2026, 3, 14), date(2026, 3, 20)


def eob_line(code, d=D1, billed=200.0, allowed=120.0, paid=96.0, resp=24.0):
    return EOBLineItem(date_of_service=d, cpt_code=code, billed=billed, allowed=allowed,
                       plan_paid=paid, patient_responsibility=resp)


def bill_line(code, d=D1, charge=200.0, balance=None):
    return BillLineItem(date_of_service=d, cpt_code=code, charge=charge, patient_balance=balance)


def clean_pair():
    eob = EOB(line_items=[eob_line("99213"), eob_line("80053", billed=100, allowed=40, paid=32, resp=8)],
              total_patient_responsibility=32.0)
    bill = Bill(line_items=[bill_line("99213"), bill_line("80053", charge=100)],
                total_charges=300, insurance_payments=128, adjustments=140, total_due=32.0)
    return bill, eob


def codes(report):
    return {f.code for f in report.findings}


# --- clean ---------------------------------------------------------------
def test_clean_bill_has_no_findings():
    r = audit(*clean_pair())
    assert r.findings == []
    assert r.expected_patient_total == 32.0
    assert r.potential_overcharge == 0.0


# --- DUPLICATE_CHARGE ----------------------------------------------------
def test_duplicate_charge_detected_and_total_flagged():
    bill, eob = clean_pair()
    bill.line_items.append(bill_line("99213"))
    bill.total_due = 232.0
    r = audit(bill, eob)
    assert "DUPLICATE_CHARGE" in codes(r)
    assert "NO_EOB_MATCH" not in codes(r)
    assert "TOTAL_EXCEEDS_RESPONSIBILITY" in codes(r)
    assert r.potential_overcharge == 200.0


def test_same_code_different_dates_is_not_duplicate():
    bill, eob = clean_pair()
    eob.line_items.append(eob_line("99213", d=D2))
    eob.total_patient_responsibility = 56.0
    bill.line_items.append(bill_line("99213", d=D2))
    bill.total_due = 56.0
    bill.insurance_payments = 224
    assert audit(bill, eob).findings == []


# --- NO_EOB_MATCH --------------------------------------------------------
def test_unprocessed_line_is_medium_not_overcharge():
    bill, eob = clean_pair()
    bill.line_items.append(bill_line("36415", charge=25))
    bill.total_due = 57.0
    r = audit(bill, eob)
    assert "NO_EOB_MATCH" in codes(r)
    assert "TOTAL_EXCEEDS_RESPONSIBILITY" not in codes(r)
    assert r.potential_overcharge == 0.0


# --- LINE_BALANCE_EXCEEDS -----------------------------------------------
def test_line_balance_exceeds():
    bill, eob = clean_pair()
    bill.line_items[0].patient_balance = 80.0
    r = audit(bill, eob)
    f = next(f for f in r.findings if f.code == "LINE_BALANCE_EXCEEDS")
    assert f.amount == 56.0


def test_line_balance_equal_is_fine():
    bill, eob = clean_pair()
    bill.line_items[0].patient_balance = 24.0
    assert "LINE_BALANCE_EXCEEDS" not in codes(audit(bill, eob))


# --- TOTAL_EXCEEDS_RESPONSIBILITY ---------------------------------------
def test_total_exceeds_after_payments():
    bill, eob = clean_pair()
    bill.patient_payments = 20.0
    bill.total_due = 32.0  # should be 12 after the $20 payment
    r = audit(bill, eob)
    f = next(f for f in r.findings if f.code == "TOTAL_EXCEEDS_RESPONSIBILITY")
    assert f.amount == 20.0
    assert r.expected_patient_total == 12.0


def test_total_uses_line_sum_when_eob_total_missing():
    bill, eob = clean_pair()
    eob.total_patient_responsibility = None
    bill.total_due = 40.0
    assert "TOTAL_EXCEEDS_RESPONSIBILITY" in codes(audit(bill, eob))


def test_total_rule_skipped_when_inputs_missing():
    bill, eob = clean_pair()
    bill.total_due = None
    assert "TOTAL_EXCEEDS_RESPONSIBILITY" not in codes(audit(bill, eob))


# --- INSURANCE_NOT_CREDITED ---------------------------------------------
def test_insurance_not_credited():
    bill, eob = clean_pair()
    bill.insurance_payments = 96.0
    f = next(f for f in audit(bill, eob).findings if f.code == "INSURANCE_NOT_CREDITED")
    assert f.amount == 32.0


# --- CHARGE_MISMATCH / EOB_LINE_NOT_ON_BILL -------------------------------
def test_charge_mismatch_is_info():
    bill, eob = clean_pair()
    bill.line_items[0].charge = 210.0
    r = audit(bill, eob)
    assert "CHARGE_MISMATCH" in codes(r)
    assert r.actionable == []


def test_eob_line_not_on_bill_is_info():
    bill, eob = clean_pair()
    eob.line_items.append(eob_line("85025", resp=0))
    r = audit(bill, eob)
    assert "EOB_LINE_NOT_ON_BILL" in codes(r)
    assert r.actionable == []


# --- matching fallback ----------------------------------------------------
def test_match_by_date_and_amount_when_code_missing():
    bill, eob = clean_pair()
    bill.line_items[1].cpt_code = None
    assert audit(bill, eob).findings == []
