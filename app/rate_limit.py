"""Ограничение частоты попыток. Сейчас нужно только для входа в админку.

Считаем ТОЛЬКО неудачные попытки, успешный вход сбрасывает счётчик, поэтому
нормальная работа администратора в лимит не упирается — упирается перебор.

Состояние живёт в памяти процесса. При нескольких воркерах лимит становится
«на воркер», а рестарт его обнуляет; для внутренней панели этого достаточно,
потому что каждая проверка пароля и так стоит PBKDF2 в 310k итераций. Если
панель поедет на несколько инстансов за балансировщиком — счётчик надо будет
перенести в общее хранилище (Redis), интерфейс класса менять не придётся.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class AttemptLimiter:
    """Скользящее окно неудачных попыток по ключу."""

    def __init__(self, max_attempts: int = 10, window_seconds: int = 900) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    # Внутреннее: вызывать только под self._lock.
    def _prune(self, key: str, now: float) -> deque[float]:
        attempts = self._failures.get(key)
        if attempts is None:
            attempts = deque()
            self._failures[key] = attempts
        while attempts and attempts[0] <= now - self.window_seconds:
            attempts.popleft()
        return attempts

    def retry_after(self, key: str) -> int:
        """0 — попытка разрешена. Иначе сколько секунд ждать до следующей."""
        with self._lock:
            now = time.monotonic()
            attempts = self._prune(key, now)
            if len(attempts) < self.max_attempts:
                return 0
            return max(int(attempts[0] + self.window_seconds - now) + 1, 1)

    def register_failure(self, key: str) -> int:
        """Записать неудачу и вернуть, сколько попыток осталось до блокировки."""
        with self._lock:
            now = time.monotonic()
            attempts = self._prune(key, now)
            attempts.append(now)
            return max(self.max_attempts - len(attempts), 0)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        """Полный сброс. Нужен тестам, чтобы лимит не протекал между ними."""
        with self._lock:
            self._failures.clear()


MAX_LOGIN_FAILURES = 10
LOGIN_WINDOW_SECONDS = 15 * 60

login_limiter = AttemptLimiter(MAX_LOGIN_FAILURES, LOGIN_WINDOW_SECONDS)


def login_key(ip: str | None, username: str) -> str:
    """Ключ лимита: пара «адрес + логин».

    Только по логину — и любой желающий блокирует вход владельцу.
    Только по адресу — и перебор по списку логинов с одного адреса
    растягивается на все учётки. Пара закрывает оба случая.
    """
    return f"{(ip or 'unknown').strip()}|{username.strip().lower()}"
