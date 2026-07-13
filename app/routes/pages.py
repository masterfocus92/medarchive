"""HTML-страницы приложения."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

# Каталог шаблонов — от положения пакета, не от cwd (см. app/main.py).
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    # Контекст пуст по контракту T1.2-BE: наполнение и вёрстка — T1.4-FE.
    return templates.TemplateResponse(request, "index.html")
