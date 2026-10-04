"""Evaluate extraction accuracy and finding recall on synthetic cases.

    python eval/run_eval.py --cases eval/cases               # full run (needs Ollama)
    python eval/run_eval.py --cases eval/cases --rules-only  # reconciler vs ground truth, no LLM
    python eval/run_eval.py --cases eval/cases --model qwen3:8b --limit 5
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from clearbill.models import EOB, Bill
from clearbill.reconcile import audit

MONEY = {"charge", "patient_balance", "billed", "allowed", "plan_paid", "deductible", "copay", "coinsurance",
         "not_covered", "patient_responsibility"}
BILL_FIELDS = ["date_of_service", "cpt_code", "charge", "patient_balance"]
EOB_FIELDS = ["date_of_service", "cpt_code", "billed", "allowed", "plan_paid", "patient_responsibility"]
BILL_TOTALS = ["insurance_payments", "patient_payments", "total_due"]


def _same(field: str, a, b) -> bool:
    if field in MONEY or field in BILL_TOTALS:
        if a is None or b is None:
            return (a or 0) == (b or 0)
        return abs(a - b) <= 0.01
    return a == b


def score_doc(pred, truth, fields, scores, prefix):
    scores[f"{prefix}.line_count"].append(len(pred.line_items) == len(truth.line_items))
    for i, t in enumerate(truth.line_items):
        p = pred.line_items[i] if i < len(pred.line_items) else None
        for f in fields:
            scores[f"{prefix}.{f}"].append(p is not None and _same(f, getattr(p, f), getattr(t, f)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=Path, default=Path("eval/cases"))
    ap.add_argument("--model", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rules-only", action="store_true")
    args = ap.parse_args()

    cases = sorted(p for p in args.cases.iterdir() if p.is_dir())[: args.limit]
    scores: dict[str, list[bool]] = defaultdict(list)
    tp = fp = fn = 0
    seconds: list[float] = []

    for case in cases:
        truth_bill = Bill.model_validate_json((case / "bill.json").read_text())
        truth_eob = EOB.model_validate_json((case / "eob.json").read_text())
        expected = set(json.loads((case / "expected.json").read_text()))

        if args.rules_only:
            bill, eob = truth_bill, truth_eob
        else:
            from clearbill.extract import extract_bill, extract_eob

            start = time.perf_counter()
            bill = extract_bill(case / "bill.pdf", args.model)
            eob = extract_eob(case / "eob.pdf", args.model)
            seconds.append(time.perf_counter() - start)
            score_doc(bill, truth_bill, BILL_FIELDS, scores, "bill")
            score_doc(eob, truth_eob, EOB_FIELDS, scores, "eob")
            for f in BILL_TOTALS:
                scores[f"bill.{f}"].append(_same(f, getattr(bill, f), getattr(truth_bill, f)))

        got = {f.code for f in audit(bill, eob).actionable}
        tp += len(got & expected)
        fp += len(got - expected)
        fn += len(expected - got)
        status = "ok " if got == expected else "MISS"
        print(f"{status} {case.name:32s} expected={sorted(expected)} got={sorted(got)}")

    print("\n== Findings ==")
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    print(f"precision {precision:.1%}   recall {recall:.1%}   (tp={tp} fp={fp} fn={fn})")

    if scores:
        print("\n== Extraction field accuracy ==")
        for k in sorted(scores):
            v = scores[k]
            print(f"{k:34s} {sum(v) / len(v):6.1%}  (n={len(v)})")
        allv = [x for v in scores.values() for x in v]
        print(f"{'OVERALL':34s} {sum(allv) / len(allv):6.1%}")
    if seconds:
        print(f"\nAvg seconds per case (bill+EOB): {sum(seconds) / len(seconds):.1f}")


if __name__ == "__main__":
    main()
