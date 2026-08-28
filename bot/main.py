"""Бот-компаньон.

У клуба уже работает бот OASys с привязкой телефона и бронированием — этот
не дублирует его, а закрывает три вещи, которые нужны программе лояльности:
кнопку запуска Mini App, приём deep-link с реферальным кодом и отправку
уведомлений (тот самый колокольчик).

Уведомления бот не получает от веб-приложения напрямую: сервисы кладут событие
в таблицу `notification_outbox` в своей транзакции, а здешний воркер
(`outbox_worker`) разбирает очередь. Так ручка API не ждёт сеть Telegram и не
падает, если Telegram недоступен, а сообщение не теряется при перезапуске.

Запуск: python -m bot.main
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import User
from app.services import notifications, referrals, sessions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
dp = Dispatcher()

# Как часто заглядываем в очередь и по сколько строк берём за раз.
# 5 секунд — компромисс между живостью уведомлений и холостыми запросами к SQLite.
OUTBOX_POLL_SECONDS = 5
OUTBOX_BATCH = 20
# Telegram режет массовую рассылку примерно на 30 сообщениях в секунду;
# держимся заметно ниже порога.
SEND_PAUSE_SECONDS = 0.05


class BotUserError(Exception):
    pass


def _open_app_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть программу лояльности", web_app=WebAppInfo(url=settings.miniapp_url))]
        ]
    )


def _phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _get_or_create(db, telegram_id: int, message: Message) -> User:
    user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
    if user is not None:
        return user

    user = User(
        telegram_id=telegram_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        referral_code=referrals.generate_code(db),
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        user = db.query(User).filter(User.telegram_id == telegram_id).one_or_none()
        if user is None:
            raise
    return user


def _save_phone(db, user: User, raw_phone: str) -> str:
    phone = sessions.normalize_phone(raw_phone)
    if not phone:
        raise BotUserError("Не удалось распознать номер. Отправь контакт кнопкой ниже ещё раз.")

    owner = db.query(User).filter(User.phone == phone, User.id != user.id).one_or_none()
    if owner is not None:
        raise BotUserError(
            "Этот номер уже привязан к другому Telegram-аккаунту. "
            "Обратись к администратору клуба."
        )

    user.phone = phone
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise BotUserError(
            "Этот номер уже привязан к другому Telegram-аккаунту. "
            "Обратись к администратору клуба."
        ) from exc
    return phone


@dp.message(CommandStart(deep_link=True))
async def start_with_referral(message: Message, command: CommandObject) -> None:
    with SessionLocal() as db:
        user = _get_or_create(db, message.from_user.id, message)
        attached = referrals.attach(db, user, command.args or "")
        needs_phone = user.phone is None
        db.commit()

    text = (
        "Ты пришёл по приглашению друга — отыграй час, и другу засчитается приглашение.\n\n"
        if attached
        else ""
    )
    if needs_phone:
        await message.answer(
            text + "Чтобы засчитывать игровые сессии, нужен номер телефона — "
            "тот же, что привязан к клубной карте.",
            reply_markup=_phone_keyboard(),
        )
        return

    await message.answer(
        text + "Открывай приложение: баланс PTS, достижения и награды внутри.",
        reply_markup=_open_app_keyboard(),
    )


@dp.message(CommandStart())
async def start(message: Message) -> None:
    with SessionLocal() as db:
        user = _get_or_create(db, message.from_user.id, message)
        needs_phone = user.phone is None
        db.commit()

    if needs_phone:
        await message.answer(
            "Привет! Чтобы засчитывать твои игровые сессии, нужен номер телефона — "
            "тот же, что привязан к клубной карте.",
            reply_markup=_phone_keyboard(),
        )
        return

    await message.answer("С возвращением. Всё внутри приложения.", reply_markup=_open_app_keyboard())


@dp.message(F.contact)
async def save_contact(message: Message) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer("Отправь, пожалуйста, свой номер, а не чужой контакт.")
        return

    try:
        with SessionLocal() as db:
            user = _get_or_create(db, message.from_user.id, message)
            _save_phone(db, user, message.contact.phone_number)
            db.commit()
    except BotUserError as exc:
        await message.answer(str(exc), reply_markup=_phone_keyboard())
        return

    await message.answer("Номер сохранён.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Готово, приложение ждёт.", reply_markup=_open_app_keyboard())


async def notify(bot: Bot, telegram_id: int, text: str) -> None:
    """Прямая отправка без очереди. Оставлена для ручных сценариев и отладки;
    бизнес-события должны идти через notifications.enqueue — только там есть
    ретраи и защита от дублей."""
    try:
        await bot.send_message(telegram_id, text, reply_markup=_open_app_keyboard())
    except Exception as exc:
        logger.warning("не доставили уведомление %s: %s", telegram_id, exc)


def _take_batch() -> list[tuple[int, int, str]]:
    """Синхронная часть: берёт пачку строк и сразу ставит им «аренду»,
    чтобы второй экземпляр бота не отправил те же сообщения второй раз.
    Возвращает простые кортежи: ORM-объекты нельзя тащить в другой поток."""
    with SessionLocal() as db:
        rows = notifications.due(db, limit=OUTBOX_BATCH)
        batch = [(row.id, row.telegram_id, row.text) for row in rows]
        notifications.lease(db, [item[0] for item in batch])
        db.commit()
    return batch


def _settle(sent_ids: list[int], failures: list[tuple[int, str]]) -> None:
    """Синхронная часть: фиксирует итог пачки одной транзакцией."""
    if not sent_ids and not failures:
        return
    with SessionLocal() as db:
        notifications.mark_sent(db, sent_ids)
        for row_id, error in failures:
            notifications.mark_failed(db, row_id, error)
        db.commit()


async def outbox_worker(bot: Bot) -> None:
    """Фоновый разбор очереди уведомлений.

    Работа с БД синхронная (SQLAlchemy Session), поэтому выносим её в поток
    через asyncio.to_thread — иначе блокируется цикл событий и бот перестаёт
    отвечать на сообщения.

    Ошибка отправки одного сообщения не роняет воркер: строка получает бэкофф
    и повторится позже; после MAX_ATTEMPTS попыток она уйдёт в failed и останется
    в таблице для разбора.
    """
    logger.info("воркер очереди уведомлений запущен")
    while True:
        try:
            batch = await asyncio.to_thread(_take_batch)
        except Exception as exc:   # noqa: BLE001 — воркер не имеет права умереть
            logger.exception("не смогли прочитать очередь уведомлений: %s", exc)
            await asyncio.sleep(OUTBOX_POLL_SECONDS)
            continue

        if not batch:
            await asyncio.sleep(OUTBOX_POLL_SECONDS)
            continue

        sent_ids: list[int] = []
        failures: list[tuple[int, str]] = []
        for row_id, telegram_id, text in batch:
            try:
                await bot.send_message(telegram_id, text, reply_markup=_open_app_keyboard())
                sent_ids.append(row_id)
            except Exception as exc:   # noqa: BLE001 — любая ошибка Telegram — повод для ретрая
                logger.warning("не доставили уведомление %s (строка %s): %s", telegram_id, row_id, exc)
                failures.append((row_id, f"{type(exc).__name__}: {exc}"))
            await asyncio.sleep(SEND_PAUSE_SECONDS)

        try:
            await asyncio.to_thread(_settle, sent_ids, failures)
        except Exception as exc:   # noqa: BLE001
            # Строки останутся pending с отложенным next_attempt_at и вернутся
            # в очередь после истечения аренды — хуже, чем дубль, но лучше потери.
            logger.exception("не смогли зафиксировать итог отправки: %s", exc)


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан в .env")
    init_db()
    bot = Bot(token=settings.bot_token)
    # Поллинг и воркер живут в одном процессе: отдельный сервис ради очереди
    # разворачивать незачем, а токен бота так остаётся в одном месте.
    await asyncio.gather(dp.start_polling(bot), outbox_worker(bot))


if __name__ == "__main__":
    asyncio.run(main())
