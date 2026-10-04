from datetime import date

from clearbill.models import EOB, Bill, BillLineItem, parse_money


def test_money_formats():
    assert parse_money("$1,204.00") == 1204.0
    assert parse_money("(12.50)") == -12.5
    assert parse_money("-3") == -3.0
    assert parse_money("40.00 CR") == -40.0
    assert parse_money("n/a") is None
    assert parse_money(19.999) == 20.0


def test_dates_and_codes_normalize():
    line = BillLineItem(date_of_service="03/14/26", cpt_code=" 99213 ", charge="$150")
    assert line.date_of_service == date(2026, 3, 14)
    assert line.cpt_code == "99213"
    assert line.charge == 150.0


def test_bad_date_becomes_none_instead_of_failing():
    assert BillLineItem(date_of_service="sometime").date_of_service is None


def test_bill_payments_forced_positive():
    assert Bill(insurance_payments="-120.00").insurance_payments == 120.0


def test_partial_page_validates():
    assert EOB.model_validate_json("{}").line_items == []
