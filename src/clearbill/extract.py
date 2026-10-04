"""Document → page images → vision LLM → validated Bill / EOB.

Pages are extracted one at a time (keeps context small on a 16GB machine) and merged:
list fields are concatenated, scalar fields take the first non-null value.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import TypeVar

from PIL import Image
from pydantic import BaseModel

from . import llm
from .config import settings
from .models import EOB, Bill

T = TypeVar("T", bound=BaseModel)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".tif", ".tiff"}

SYSTEM = (
    "You are a meticulous data-entry clerk for medical billing documents. "
    "Copy values exactly as printed. Never calculate, infer or guess values. "
    "If a field is not visible on this page, use null."
)

BILL_PROMPT = """This image is one page of a medical provider's bill / patient statement.
Extract it into the JSON schema.
- line_items: one entry per service row. cpt_code is the 5-character procedure code if printed.
- charge: the amount charged for that row. patient_balance: per-row amount due only if printed.
- Money as plain numbers (no $ or commas). Dates as YYYY-MM-DD.
- insurance_payments, adjustments, patient_payments: totals as positive numbers.
- total_due: the amount the statement says the patient owes now.
- If this page has no service rows, return an empty line_items list."""

EOB_PROMPT = """This image is one page of an insurance Explanation of Benefits (EOB).
Extract it into the JSON schema.
- line_items: one entry per service row with its date, CPT code, billed, allowed, plan_paid,
  deductible, copay, coinsurance, not_covered and patient_responsibility columns.
- Money as plain numbers (no $ or commas). Dates as YYYY-MM-DD.
- total_patient_responsibility: the claim total the patient may owe, if printed.
- If this page has no service rows, return an empty line_items list."""


def _to_png(img: Image.Image) -> bytes:
    img = img.convert("RGB")
    img.thumbnail((settings.max_image_px, settings.max_image_px))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def document_to_images(path: str | Path) -> list[bytes]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        try:
            return [_to_png(page.render(scale=settings.render_dpi / 72).to_pil()) for page in pdf]
        finally:
            pdf.close()
    if suffix in IMAGE_SUFFIXES:
        if suffix == ".heic":
            try:
                from pillow_heif import register_heif_opener

                register_heif_opener()
            except ImportError as e:
                raise RuntimeError("HEIC photos need `pip install pillow-heif`") from e
        return [_to_png(Image.open(path))]
    raise ValueError(f"Unsupported file type: {suffix}")


def merge_pages(pages: list[T], schema: type[T]) -> T:
    merged: dict = {}
    for name in schema.model_fields:
        values = [getattr(p, name) for p in pages]
        if isinstance(getattr(schema(), name), list):
            merged[name] = [item for v in values for item in v]
        else:
            merged[name] = next((v for v in values if v is not None), None)
    return schema.model_validate(merged)


def _extract(path: str | Path, schema: type[T], prompt: str, model: str | None) -> T:
    model = model or settings.vision_model
    pages = [
        llm.structured(model, prompt, schema, system=SYSTEM, images=[img])
        for img in document_to_images(path)
    ]
    return merge_pages(pages, schema)


def extract_bill(path: str | Path, model: str | None = None) -> Bill:
    return _extract(path, Bill, BILL_PROMPT, model)


def extract_eob(path: str | Path, model: str | None = None) -> EOB:
    return _extract(path, EOB, EOB_PROMPT, model)
