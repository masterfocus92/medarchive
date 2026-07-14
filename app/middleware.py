"""Закрытость приложения по умолчанию.

Инвариант: файлы и записи недоступны без аутентификации. Механизм —
default-deny: роут защищён самим фактом существования, публичность —
явное исключение в списке ниже. Новый роут будущего этапа не может
«забыть про auth».
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

# Публичные пути — исчерпывающий список. Расширять сознательно.
PUBLIC_PATHS = {"/login", "/health"}
# Статика приложения (стили, шрифты) нужна экрану входа. Файлы записей
# раздаются НЕ отсюда (FILES_DIR вне static) — на них default-deny action.
PUBLIC_PREFIXES = ("/static/",)

SESSION_ACCOUNT_KEY = "account_id"


class AuthRequiredMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        is_public = path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
        if is_public or SESSION_ACCOUNT_KEY in request.session:
            return await call_next(request)
        return RedirectResponse("/login", status_code=303)
