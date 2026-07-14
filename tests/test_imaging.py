"""Тесты подготовки изображений (T4.2): провайдер принимает только
png/jpeg/webp/gif (ADR-014) — HEIC и PDF конвертируем сами."""

import io

import pillow_heif
import pypdfium2 as pdfium
import pytest
from PIL import Image

from app.services.imaging import MAX_SIDE, prepare_for_vision


def _jpeg(width=800, height=600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="JPEG")
    return buf.getvalue()


def _png(width=800, height=600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


def _heic() -> bytes:
    heif = pillow_heif.from_pillow(Image.new("RGB", (640, 480), "white"))
    buf = io.BytesIO()
    heif.save(buf, quality=80)
    return buf.getvalue()


def _pdf(pages=2) -> bytes:
    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(400, 600)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def test_small_jpeg_passes_unchanged():
    content = _jpeg()

    result = prepare_for_vision([("image/jpeg", content)])

    assert result == [("image/jpeg", content)]


def test_oversized_image_is_downscaled():
    result = prepare_for_vision([("image/png", _png(width=MAX_SIDE * 2, height=MAX_SIDE))])

    (mime, content) = result[0]
    image = Image.open(io.BytesIO(content))
    assert max(image.size) <= MAX_SIDE


def test_heic_becomes_jpeg():
    result = prepare_for_vision([("image/heic", _heic())])

    (mime, content) = result[0]
    assert mime == "image/jpeg"
    assert Image.open(io.BytesIO(content)).format == "JPEG"


def test_pdf_becomes_png_per_page():
    result = prepare_for_vision([("application/pdf", _pdf(pages=2))])

    assert len(result) == 2
    assert all(mime == "image/png" for mime, _ in result)
    assert Image.open(io.BytesIO(result[0][1])).format == "PNG"


def test_page_order_is_preserved_across_mixed_sources():
    result = prepare_for_vision(
        [("image/jpeg", _jpeg()), ("application/pdf", _pdf(pages=2)), ("image/png", _png())]
    )

    assert [mime for mime, _ in result] == ["image/jpeg", "image/png", "image/png", "image/png"]


def test_unknown_mime_rejected():
    with pytest.raises(ValueError):
        prepare_for_vision([("application/zip", b"PK...")])
