"""Подготовка изображений для vision-провайдера (T4.2, ADR-014).

Провайдер принимает только png/jpeg/webp/gif — HEIC (кадры iPhone, ❓8)
и PDF конвертируем на своей стороне. Крупные кадры ужимаются: 12 Мп
с телефона — это лишние токены без пользы для разбора.
"""

import io

import pillow_heif
import pypdfium2 as pdfium
from PIL import Image

pillow_heif.register_heif_opener()

# Длинной стороны в 1600px достаточно для чтения меддокумента моделью;
# кратно меньше токенов, чем у оригинала с телефона.
MAX_SIDE = 1600
JPEG_QUALITY = 85
# PDF рендерим с двукратным масштабом: базовые 72 dpi нечитаемы для мелкого шрифта.
PDF_RENDER_SCALE = 2.0

# Форматы, которые провайдер ест как есть (ADR-014).
PASSTHROUGH_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def prepare_for_vision(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    """(mime, содержимое) страниц записи → страницы для vision-запроса.

    Порядок сохраняется; PDF разворачивается в страницу-на-изображение.
    """
    pages: list[tuple[str, bytes]] = []
    for mime, content in files:
        if mime == "application/pdf":
            pages.extend(_pdf_to_pngs(content))
        elif mime == "image/heic":
            pages.append(("image/jpeg", _to_jpeg(Image.open(io.BytesIO(content)))))
        elif mime in PASSTHROUGH_MIMES:
            pages.append(_downscale_if_needed(mime, content))
        else:
            raise ValueError(f"Формат {mime} не поддерживается подготовкой изображений")
    return pages


def _pdf_to_pngs(content: bytes) -> list[tuple[str, bytes]]:
    pdf = pdfium.PdfDocument(content)
    pages = []
    for page in pdf:
        image = page.render(scale=PDF_RENDER_SCALE).to_pil()
        image = _shrink(image)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        pages.append(("image/png", buf.getvalue()))
    return pages


def _downscale_if_needed(mime: str, content: bytes) -> tuple[str, bytes]:
    image = Image.open(io.BytesIO(content))
    if max(image.size) <= MAX_SIDE:
        # Не перекодируем без нужды: байты остаются оригинальными.
        return (mime, content)
    return ("image/jpeg", _to_jpeg(image))


def _to_jpeg(image: Image.Image) -> bytes:
    image = _shrink(image)
    if image.mode != "RGB":
        image = image.convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def _shrink(image: Image.Image) -> Image.Image:
    if max(image.size) <= MAX_SIDE:
        return image
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image
