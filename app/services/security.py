"""Работа с паролями.

bcrypt — медленный по построению: перебор паролей по утёкшему дампу БД
дорог. Библиотека bcrypt используется напрямую, без passlib: passlib
не обновляется с 2020 года и несовместим с bcrypt >= 5 (тот перестал
молча усекать пароли длиннее 72 байт).

Единственное место работы с паролями в проекте: seed создаёт хэши здесь,
auth (T2.2) добавит сюда же verify_password — алгоритм и его параметры
живут в одной точке.
"""

import bcrypt

# Фиктивный хэш для выравнивания времени ответа: при несуществующем
# email всё равно выполняется полная bcrypt-проверка, иначе быстрый
# ответ выдаёт «такого email нет» по таймингу. Хэш не соответствует
# никакому реальному паролю.
DUMMY_HASH = "$2b$12$T2qm9tf7hYIPVxQHUO5hYehyaYnxp1GVZb1DdA0cOC5XffIbgUcOu"


def verify_password(password: str, password_hash: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > 72:
        # Симметрично hash_password: такой пароль не мог быть захэширован.
        return False
    return bcrypt.checkpw(raw, password_hash.encode("ascii"))


def hash_password(password: str) -> str:
    # bcrypt смотрит только на первые 72 байта. Для наших паролей это
    # заведомо достаточно; ограничение проверяем явно, чтобы более
    # длинный пароль не «совпадал» с чужим по первым 72 байтам молча.
    raw = password.encode("utf-8")
    if len(raw) > 72:
        raise ValueError("Пароль длиннее 72 байт — bcrypt его не примет")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")
