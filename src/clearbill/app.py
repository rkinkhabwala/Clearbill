"""Streamlit UI:  streamlit run src/clearbill/app.py"""
from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from clearbill.config import settings
from clearbill.models import EOB, Bill
from clearbill.reconcile import Severity, audit

st.set_page_config(page_title="ClearBill", page_icon="🧾", layout="wide")
st.title("🧾 ClearBill")
st.caption(f"Local bill vs EOB auditor · models run on your Mac via Ollama ({settings.vision_model}) · nothing leaves this machine")


def _save(upload) -> Path:
    tmp = Path(tempfile.mkdtemp()) / upload.name
    tmp.write_bytes(upload.getvalue())
    return tmp


col1, col2 = st.columns(2)
bill_file = col1.file_uploader("Provider bill (PDF or photo)", type=["pdf", "png", "jpg", "jpeg", "webp"])
eob_file = col2.file_uploader("Insurance EOB (PDF or photo)", type=["pdf", "png", "jpg", "jpeg", "webp"])

if bill_file and eob_file and st.button("Read documents", type="primary"):
    from clearbill.extract import extract_bill, extract_eob

    with st.spinner("Reading bill…"):
        st.session_state.bill = extract_bill(_save(bill_file)).model_dump_json(indent=2)
    with st.spinner("Reading EOB…"):
        st.session_state.eob = extract_eob(_save(eob_file)).model_dump_json(indent=2)

if "bill" in st.session_state:
    st.subheader("1 · Review what was read")
    st.caption("Fix anything the model misread before auditing.")
    c1, c2 = st.columns(2)
    bill_json = c1.text_area("Bill", st.session_state.bill, height=320)
    eob_json = c2.text_area("EOB", st.session_state.eob, height=320)

    if st.button("Audit", type="primary"):
        try:
            b, e = Bill.model_validate_json(bill_json), EOB.model_validate_json(eob_json)
        except Exception as ex:  # noqa: BLE001
            st.error(f"JSON is invalid: {ex}")
            st.stop()
        report = audit(b, e)

        st.subheader("2 · Results")
        m1, m2, m3 = st.columns(3)
        m1.metric("Bill says you owe", f"${report.billed_total_due:,.2f}" if report.billed_total_due is not None else "—")
        m2.metric("EOB says you owe", f"${report.expected_patient_total:,.2f}" if report.expected_patient_total is not None else "—")
        m3.metric("Potential overcharge", f"${report.potential_overcharge:,.2f}")

        icon = {Severity.high: "🔴", Severity.medium: "🟠", Severity.info: "⚪"}
        for f in report.findings:
            with st.expander(f"{icon[f.severity]} {f.title}" + (f" — ${f.amount:,.2f}" if f.amount else ""),
                             expanded=f.severity != Severity.info):
                st.write(f.detail)
        if not report.findings:
            st.success("No discrepancies — the bill matches the EOB.")

        if report.actionable:
            from clearbill.explain import dispute_letter, summarize

            with st.spinner("Explaining…"):
                st.subheader("3 · In plain English")
                st.write(summarize(report, b, e))
            with st.spinner("Drafting letter…"):
                letter = dispute_letter(report, b, e)
            st.subheader("4 · Draft dispute letter")
            st.text_area("Letter", letter, height=360)
            st.download_button("Download letter", letter, file_name="dispute_letter.txt")
        st.caption("ClearBill is informational, not legal or medical advice. Review everything before sending.")
