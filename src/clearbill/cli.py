"""ClearBill command line.

  clearbill doctor                         check Ollama + models
  clearbill extract FILE --kind bill|eob   print extracted JSON
  clearbill audit BILL EOB [--json-input] [--no-llm] [--out report.md]
"""
from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path

import typer

from .config import settings
from .models import EOB, Bill
from .reconcile import audit as run_audit

app = typer.Typer(add_completion=False, help="Local medical bill vs EOB auditor (runs on Ollama).")


class Kind(str, Enum):
    bill = "bill"
    eob = "eob"


@app.callback()
def _main(verbose: bool = typer.Option(False, "--verbose", "-v")):
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(message)s")


@app.command()
def doctor():
    """Check that Ollama is running and the configured models are pulled."""
    from . import llm

    try:
        have = llm.available_models()
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ Cannot reach Ollama at {settings.ollama_host}: {e}", fg="red")
        typer.echo("  Start it with:  open -a Ollama   (or `ollama serve`)")
        raise typer.Exit(1)
    typer.secho(f"✓ Ollama reachable at {settings.ollama_host}", fg="green")
    ok = True
    for m in sorted({settings.vision_model, settings.text_model}):
        if m in have:
            typer.secho(f"✓ model {m}", fg="green")
        else:
            ok = False
            typer.secho(f"✗ model {m} missing — run: ollama pull {m}", fg="red")
    raise typer.Exit(0 if ok else 1)


@app.command()
def extract(file: Path, kind: Kind = typer.Option(..., "--kind"), model: str = typer.Option(None)):
    """Extract a bill or EOB from a PDF/photo and print JSON."""
    from .extract import extract_bill, extract_eob

    doc = extract_bill(file, model) if kind is Kind.bill else extract_eob(file, model)
    typer.echo(doc.model_dump_json(indent=2))


@app.command()
def audit(
    bill: Path,
    eob: Path,
    json_input: bool = typer.Option(False, "--json-input", help="BILL/EOB are JSON files; skip extraction"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip the LLM summary and letter"),
    out: Path = typer.Option(None, "--out", help="Write the report (and letter) here"),
):
    """Reconcile a bill against its EOB."""
    if json_input:
        b = Bill.model_validate_json(bill.read_text())
        e = EOB.model_validate_json(eob.read_text())
    else:
        from .extract import extract_bill, extract_eob

        typer.echo("Reading bill…", err=True)
        b = extract_bill(bill)
        typer.echo("Reading EOB…", err=True)
        e = extract_eob(eob)

    report = run_audit(b, e)
    parts = [report.to_markdown()]
    if not no_llm and report.actionable:
        from .explain import dispute_letter, summarize

        typer.echo("Writing explanation…", err=True)
        parts.insert(0, "## In plain English\n\n" + summarize(report, b, e) + "\n")
        letter = dispute_letter(report, b, e)
        if letter:
            parts.append("---\n\n## Draft dispute letter\n\n" + letter)
    text = "\n".join(parts)
    if out:
        out.write_text(text)
        typer.echo(f"Wrote {out}", err=True)
    else:
        typer.echo(text)


@app.command("save-json")
def save_json(bill: Path, eob: Path, out_dir: Path = typer.Option(Path("."), "--out-dir")):
    """Extract both documents and save JSON so you can review/edit before auditing."""
    from .extract import extract_bill, extract_eob

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "bill.json").write_text(extract_bill(bill).model_dump_json(indent=2))
    (out_dir / "eob.json").write_text(extract_eob(eob).model_dump_json(indent=2))
    typer.echo(f"Saved {out_dir/'bill.json'} and {out_dir/'eob.json'}. Edit if needed, then:\n"
               f"  clearbill audit {out_dir/'bill.json'} {out_dir/'eob.json'} --json-input")


if __name__ == "__main__":
    app()
