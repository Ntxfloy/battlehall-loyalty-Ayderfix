"""Бот-компаньон.

У клуба уже работает бот OASys с привязкой телефона и бронированием — этот
не дублирует его, а закрывает три вещи, которые нужны программе лояльности:
кнопку запуска Mini App, приём deep-link с реферальным кодом и отправку
уведомлений (тот самый колокольчик).

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

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import User
from app.services import referrals, sessions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
dp = Dispatcher()


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
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            referral_code=referrals.generate_code(db),
        )
        db.add(user)
        db.flush()
    return user


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

    phone = sessions.normalize_phone(message.contact.phone_number)
    with SessionLocal() as db:
        user = _get_or_create(db, message.from_user.id, message)
        user.phone = phone
        db.add(user)
        db.commit()

    await message.answer("Номер сохранён.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Готово, приложение ждёт.", reply_markup=_open_app_keyboard())


async def notify(bot: Bot, telegram_id: int, text: str) -> None:
    try:
        await bot.send_message(telegram_id, text, reply_markup=_open_app_keyboard())
    except Exception as exc:
        logger.warning("не доставили уведомление %s: %s", telegram_id, exc)


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан в .env")
    init_db()
    bot = Bot(token=settings.bot_token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
