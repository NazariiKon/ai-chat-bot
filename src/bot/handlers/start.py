from telegram import Update
from telegram.ext import ContextTypes

from bot.services.db_service import db_service

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for the /start command"""
    user_name = update.effective_user.first_name
    chat_id = update.effective_chat.id
    
    # Set default nickname if not set
    settings = await db_service.get_chat_settings(chat_id)
    if not settings or not settings.bot_nickname:
        await db_service.set_chat_nickname(chat_id, "Вася")
    
    await update.message.reply_text(
        f"Привіт, {user_name}! Давай знайомитись. 😊\n\n"
        "За замовчуванням мене звати <b>Вася</b>, але ти можеш дати мені будь-яке ім'я та манеру поведінки. \n"
        "Просто напиши: <i>'Тебе звати [Ім'я]'</i> або <i>'Будь [роль]'</i>. \n\n"
        "Я також запам'ятовую факти про тебе, щоб спілкування було живим.\n"
        "Ще ти можеш керувати шансом моєї спонтанної відповіді командою /set_response_chance.\n"
        "Приклад: /set_response_chance 20% або /set_response_chance 0.2",
        parse_mode="HTML"
    )
