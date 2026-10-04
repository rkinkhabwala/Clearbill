"""Pydantic schemas shared by extraction, reconciliation and the UI.

Every field is optional so that a single page of a multi-page document still validates.
Validators normalize the messy formats vision models (and real bills) produce.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%b %d, %Y", "%B %d, %Y")


def parse_date(value):
    if value is None or isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None  # unparseable dates become missing rather than failing the whole page


def parse_money(value):
    if value is None or isinstance(value, (int, float)):
        return None if value is None else round(float(value), 2)
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "-"}:
        return None
    negative = text.startswith("(") and text.endswith(")") or text.startswith("-") or text.endswith("CR")
    digits = re.sub(r"[^0-9.]", "", text)
    if not digits:
        return None
    amount = round(float(digits), 2)
    return -amount if negative else amount


def normalize_code(value):
    if value is None:
        return None
    code = re.sub(r"\s+", "", str(value)).upper()
    return code or None


class _Base(BaseModel):
    model_config = {"extra": "ignore"}


class BillLineItem(_Base):
    date_of_service: date | None = None
    cpt_code: str | None = Field(None, description="5-character CPT/HCPCS code, e.g. 99213")
    description: str | None = None
    charge: float | None = Field(None, description="Amount the provider charged for this line")
    patient_balance: float | None = Field(None, description="Amount the patient owes for this line, if shown")

    v_date = field_validator("date_of_service", mode="before")(parse_date)
    v_code = field_validator("cpt_code", mode="before")(normalize_code)
    v_money = field_validator("charge", "patient_balance", mode="before")(parse_money)


class Bill(_Base):
    provider_name: str | None = None
    statement_date: date | None = None
    account_number: str | None = None
    line_items: list[BillLineItem] = Field(default_factory=list)
    total_charges: float | None = None
    insurance_payments: float | None = Field(None, description="Total insurance payments shown as a positive number")
    adjustments: float | None = Field(None, description="Total contractual adjustments shown as a positive number")
    patient_payments: float | None = Field(None, description="Payments already made by the patient, positive number")
    total_due: float | None = Field(None, description="Amount the bill says the patient owes now")

    v_date = field_validator("statement_date", mode="before")(parse_date)
    v_money = field_validator(
        "total_charges", "insurance_payments", "adjustments", "patient_payments", "total_due", mode="before"
    )(parse_money)

    @field_validator("insurance_payments", "adjustments", "patient_payments")
    @classmethod
    def v_positive(cls, v):
        return abs(v) if v is not None else v


class EOBLineItem(_Base):
    date_of_service: date | None = None
    cpt_code: str | None = None
    description: str | None = None
    billed: float | None = None
    allowed: float | None = None
    plan_paid: float | None = None
    deductible: float | None = None
    copay: float | None = None
    coinsurance: float | None = None
    not_covered: float | None = None
    patient_responsibility: float | None = Field(None, description="What the patient may owe for this line")

    v_date = field_validator("date_of_service", mode="before")(parse_date)
    v_code = field_validator("cpt_code", mode="before")(normalize_code)
    v_money = field_validator(
        "billed", "allowed", "plan_paid", "deductible", "copay", "coinsurance", "not_covered",
        "patient_responsibility", mode="before",
    )(parse_money)


class EOB(_Base):
    payer_name: str | None = None
    provider_name: str | None = None
    claim_number: str | None = None
    line_items: list[EOBLineItem] = Field(default_factory=list)
    total_patient_responsibility: float | None = None

    v_money = field_validator("total_patient_responsibility", mode="before")(parse_money)
