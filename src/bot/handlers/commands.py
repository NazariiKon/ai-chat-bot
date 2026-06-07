import json
import json
from telegram import Update
from telegram.ext import ContextTypes
from bot.config import settings
from bot.services.db_service import db_service

async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the user what the bot knows about them in JSON format."""
    user = update.effective_user
    user_data = await db_service.get_user(user.id)
    persona_data = await db_service.get_persona(user.id)

    payload = {
        "tg_id": user.id,
        "username": user.username or None,
        "first_name": user.first_name,
        "message_count": user_data.message_count if user_data else 0,
        "persona": persona_data or {},
    }

    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    await update.message.reply_text(f"```json\n{json_text}\n```", parse_mode="Markdown")


async def set_response_chance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets or displays the spontaneous response chance for this chat."""
    chat_id = update.effective_chat.id
    args = context.args or []

    if not args:
        settings = await db_service.get_chat_settings(chat_id)
        chance = (
            settings.spontaneous_response_chance
            if settings and settings.spontaneous_response_chance is not None
            else None
        )
        if chance is None:
            await update.message.reply_text(
                "Поточний шанс спонтанної відповіді не встановлений для цього чату. "
                f"Використовується глобальний рівень {settings.SPONTANEOUS_RESPONSE_CHANCE:.0%}."
            )
            return

        await update.message.reply_text(
            f"Поточний шанс спонтанної відповіді: {chance:.0%}."
        )
        return

    raw_value = args[0].strip()
    if raw_value.endswith("%"):
        raw_value = raw_value[:-1].strip()
        value_type = "percent"
    else:
        value_type = "fraction"

    try:
        chance_value = float(raw_value)
    except ValueError:
        await update.message.reply_text(
            "Неправильний формат. Введи число від 0 до 1 або від 0 до 100%, наприклад 0.15 або 15%."
        )
        return

    if value_type == "percent":
        chance_value /= 100.0
    elif chance_value > 1:
        chance_value /= 100.0

    if chance_value < 0.0 or chance_value > 1.0:
        await update.message.reply_text(
            "Значення має бути між 0 і 1 (наприклад 0.2) або між 0% і 100% (наприклад 20%)."
        )
        return

    await db_service.set_spontaneous_response_chance(chat_id, chance_value)
    await update.message.reply_text(
        f"Шанс спонтанної відповіді оновлено: {chance_value:.0%}."
    )


async def forget_me_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clears the user's persona from the database."""
    user = update.effective_user
    await db_service.clear_persona(user.id)
    await update.message.reply_text("Добре, я все забув. Почнемо з чистого листа? 😉")

async def bot_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the bot's current nickname and persona in the chat."""
    chat_id = update.effective_chat.id
    settings = await db_service.get_chat_settings(chat_id)
    
    if not settings:
        await update.message.reply_text("Налаштування для цього чату ще не створені.")
        return

    nickname = settings.bot_nickname or "Не встановлено"
    persona_text = settings.bot_persona or "Стандартна"
    try:
        persona_obj = json.loads(settings.bot_persona)
        persona_text = json.dumps(persona_obj, ensure_ascii=False, indent=2)
    except Exception:
        persona_text = settings.bot_persona or "Стандартна"
    
    report = (
        f"<b>🤖 Інформація про бота в цьому чаті:</b>\n\n"
        f"<b>Нікнейм:</b> {nickname}\n"
        f"<b>Персона/Стиль:</b>\n<code>{persona_text}</code>"
    )
    await update.message.reply_html(report)
