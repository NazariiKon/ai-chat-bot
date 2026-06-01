import re
import logging
import random
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ContextTypes
from bot.services.ai_service import ai_service
from bot.config import settings
from bot.services.db_service import db_service
from bot.prompt_builder import (
    build_personas_context,
    build_system_prompt,
    build_messages,
)

def get_message_content(update: Update) -> str:
    """
    Extracts text or creates a placeholder for media messages.
    """
    message = update.message
    if message.text:
        return message.text
    if message.photo:
        return "[📷 Photo]"
    if message.sticker:
        return "[🎨 Sticker]"
    if message.document:
        filename = message.document.file_name or "document"
        return f"[📁 Document: {filename}]"
    if message.voice:
        return "[🎤 Voice Message]"
    if message.video:
        return "[🎥 Video]"
    return "[📦 Media/Other]"


MEMORY_UPDATE_PATTERN = re.compile(r"\[MEMORY_UPDATE:\s*(.*?)\]", re.IGNORECASE)
MEMORY_REMOVE_PATTERN = re.compile(r"\[MEMORY_REMOVE:\s*(.*?)\]", re.IGNORECASE)
STYLE_UPDATE_PATTERN = re.compile(r"\[STYLE_UPDATE:\s*(.*?)\]", re.IGNORECASE)
NAME_UPDATE_PATTERN = re.compile(r"\[NAME_UPDATE:\s*(.*?)\]", re.IGNORECASE)
SKIP_PATTERN = re.compile(r"\[SKIP\]", re.IGNORECASE)
QUESTION_TRIGGER_WORDS = re.compile(r"\b(чому|що|яка|який|які|хто|як|коли|де|навіщо|скільки)\b", re.IGNORECASE)


def resolve_persona_target(response_text: str, participants, default_user_id: int) -> int:
    """Resolve which user should receive a memory update based on mentions or name matches."""
    if not response_text:
        return default_user_id

    text = response_text
    mention_matches = re.findall(r"@([A-Za-z0-9_]+)", text)
    for mention in mention_matches:
        for participant in participants:
            if participant.username and participant.username.lstrip("@").lower() == mention.lower():
                return participant.tg_id

    for participant in participants:
        if participant.first_name:
            name_re = re.compile(rf"(?<!\w){re.escape(participant.first_name)}(?!\w)", re.IGNORECASE)
            if name_re.search(text):
                return participant.tg_id

    return default_user_id


def looks_like_question(text: str) -> bool:
    if not text:
        return False
    return bool(QUESTION_TRIGGER_WORDS.search(text)) or "?" in text


def parse_ai_tags(response_text: str) -> dict:
    return {
        "memory_updates": MEMORY_UPDATE_PATTERN.findall(response_text),
        "memory_removals": MEMORY_REMOVE_PATTERN.findall(response_text),
        "style_updates": STYLE_UPDATE_PATTERN.findall(response_text),
        "name_updates": NAME_UPDATE_PATTERN.findall(response_text),
        "skip": bool(SKIP_PATTERN.search(response_text)),
    }


def clean_ai_response(response_text: str) -> str:
    clean_reply = MEMORY_UPDATE_PATTERN.sub("", response_text)
    clean_reply = MEMORY_REMOVE_PATTERN.sub("", clean_reply)
    clean_reply = STYLE_UPDATE_PATTERN.sub("", clean_reply)
    clean_reply = NAME_UPDATE_PATTERN.sub("", clean_reply)
    clean_reply = SKIP_PATTERN.sub("", clean_reply)

    # Remove any leftover lines containing technical tags.
    clean_reply = re.sub(
        r"(?mi)^.*(MEMORY_UPDATE|MEMORY_REMOVE|STYLE_UPDATE|NAME_UPDATE|SKIP).*$",
        "",
        clean_reply,
    )
    clean_reply = re.sub(r"\n{3,}", "\n\n", clean_reply).strip()
    return clean_reply

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming messages with custom nickname awareness and global context."""
    if not update.message:
        return

    message_date = update.message.date
    if message_date:
        now = datetime.now(timezone.utc)
        if message_date.tzinfo is None:
            message_date = message_date.replace(tzinfo=timezone.utc)
        age_seconds = (now - message_date).total_seconds()
        if age_seconds > settings.MAX_MESSAGE_AGE_SECONDS:
            logging.info(
                f"Ignoring stale message ({age_seconds:.0f}s old) in chat {update.message.chat_id}"
            )
            return

    chat_id = update.message.chat_id
    user = update.effective_user
    text = update.message.text or update.message.caption or ""
    if not text:
        text = get_message_content(update)

    # 1. Passive logging & User update
    await db_service.upsert_user(user.id, user.username or "", user.first_name)
    await db_service.save_message(chat_id, user.id, "user", text)

    # 2. Get Chat Settings (Nickname)
    chat_settings = await db_service.get_chat_settings(chat_id)
    bot_nickname = chat_settings.bot_nickname if chat_settings else None

    # 3. Decision: Should we respond?
    is_private = update.message.chat.type == "private"
    bot_username = (context.bot.username or settings.BOT_USERNAME or "").lstrip("@").lower()
    text_lower = text.lower()

    is_mention = False
    if bot_username:
        is_mention = f"@{bot_username}" in text_lower
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
        is_mention = True

    message_entities = list(update.message.entities or [])
    if update.message.caption_entities:
        message_entities.extend(update.message.caption_entities)

    for entity in message_entities:
        if hasattr(entity, "type") and entity.type in ["mention", "text_mention"]:
            if entity.type == "mention":
                entity_text = text_lower[entity.offset: entity.offset + entity.length]
                if entity_text == f"@{bot_username}":
                    is_mention = True
                    break
            elif entity.type == "text_mention" and getattr(entity, "user", None):
                if entity.user.id == context.bot.id:
                    is_mention = True
                    break

    is_nickname = False
    if bot_nickname:
        escaped_nickname = re.escape(bot_nickname.lower())
        is_nickname = bool(re.search(rf"(^|\W){escaped_nickname}(\W|$)", text_lower))

    # Chance to join group discussion without being mentioned
    is_spontaneous = False
    if not is_mention and not is_nickname and update.message.chat.type in ["group", "supergroup"]:
        spontaneous_chance = (
            chat_settings.spontaneous_response_chance
            if chat_settings and chat_settings.spontaneous_response_chance is not None
            else settings.SPONTANEOUS_RESPONSE_CHANCE
        )
        if random.random() < spontaneous_chance:
            if not settings.SPONTANEOUS_ONLY_QUESTION or looks_like_question(text):
                is_spontaneous = True

    notify_on_error = not is_spontaneous

    if not (is_private or is_mention or is_nickname or is_spontaneous):
        # Allow AI to process if it looks like the user is trying to name the bot
        name_triggers = ["називай себе", "тебе звати", "твоє ім'я", "відтепер ти"]
        if not bot_nickname and any(t in text.lower() for t in name_triggers):
            pass 
        else:
            return

    # Send "typing" action
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # 4. GLOBAL Context Gathering
    history = await db_service.get_recent_messages(chat_id)
    participants = await db_service.get_chat_participants(chat_id)
    personas_context = build_personas_context(participants)

    # 5. System Prompt Update
    bot_style = chat_settings.bot_persona if chat_settings and chat_settings.bot_persona else "Звичайна, дружня людина, частина компанії."
    display_name = bot_nickname or "Бот (ім'я ще не встановлено)"
    current_system_prompt = build_system_prompt(display_name, bot_style, personas_context)
    messages = build_messages(history, current_system_prompt)

    if ai_service is None:
        logging.error("AI service is not initialized, skipping message processing")
        if notify_on_error:
            await update.message.reply_text("Я тимчасово недоступний. Спробуй пізніше.")
        return

    # 6. AI response
    try:
        response_text = await ai_service.get_response(messages)
        print(f"DEBUG: AI Raw Response: {response_text}") # Added for better visibility
        
        # 1. Look for User Memory Updates
        persona_target_id = resolve_persona_target(response_text, participants, user.id)
        memory_pattern = r"\[MEMORY_UPDATE:\s*(.*?)\]"
        user_updates = re.findall(memory_pattern, response_text)
        for fact in user_updates:
            await db_service.update_persona(persona_target_id, fact)
            logging.info(f"Learned new fact for user {persona_target_id}: {fact}")

        # 2. Look for User Memory Removal
        remove_pattern = r"\[MEMORY_REMOVE:\s*(.*?)\]"
        remove_requests = re.findall(remove_pattern, response_text)
        for keywords in remove_requests:
            await db_service.remove_from_persona(persona_target_id, keywords)
            logging.info(f"Removed fact containing '{keywords}' from user {persona_target_id}")

        # 3. Look for Style Updates
        style_pattern = r"\[STYLE_UPDATE:\s*(.*?)\]"
        style_updates = re.findall(style_pattern, response_text)
        for style in style_updates:
            await db_service.update_bot_persona(chat_id, style)
            logging.info(f"Bot adapted new style in chat {chat_id}: {style}")

        # 3. Look for Name Updates
        name_pattern = r"\[NAME_UPDATE:\s*(.*?)\]"
        name_updates = re.findall(name_pattern, response_text)
        for new_name in name_updates:
            clean_name = new_name.strip().strip(' .!').capitalize()
            await db_service.set_chat_nickname(chat_id, clean_name)
            logging.info(f"Bot renamed to {clean_name} in chat {chat_id}")

        # 4. Clean and Send
        if SKIP_PATTERN.search(response_text):
            logging.info(f"AI decided to skip spontaneous response in chat {chat_id}")
            return

        clean_reply = clean_ai_response(response_text)

        if clean_reply:
            await db_service.save_message(chat_id, context.bot.id, "assistant", clean_reply)
            await update.message.reply_text(clean_reply)
            
    except Exception as e:
        logging.error(f"AI Error: {e}")
        if notify_on_error:
            await update.message.reply_text("Помилка зв'язку з мізками... (AI Error)")
