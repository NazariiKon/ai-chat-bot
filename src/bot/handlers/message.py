import re
import json
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
PERSONA_UPDATE_JSON_PATTERN = re.compile(r"\[PERSONA_UPDATE_JSON:\s*", re.IGNORECASE)
BOT_PERSONA_UPDATE_JSON_PATTERN = re.compile(r"\[BOT_PERSONA_UPDATE_JSON:\s*", re.IGNORECASE)
MEMORY_REMOVE_PATTERN = re.compile(r"\[MEMORY_REMOVE:\s*(.*?)\]", re.IGNORECASE)
STYLE_UPDATE_PATTERN = re.compile(r"\[STYLE_UPDATE:\s*(.*?)\]", re.IGNORECASE)
NAME_UPDATE_PATTERN = re.compile(r"\[NAME_UPDATE:\s*(.*?)\]", re.IGNORECASE)
SKIP_PATTERN = re.compile(r"\[SKIP\]", re.IGNORECASE)


def extract_json_object(text: str, start_index: int) -> str | None:
    depth = 0
    in_string = False
    escape = False
    for idx in range(start_index, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_index: idx + 1]
    return None


def find_tagged_json_objects(text: str, tag: str) -> list[str]:
    tag_prefix = f"[{tag}:"
    results = []
    cursor = 0

    while True:
        tag_start = text.lower().find(tag_prefix.lower(), cursor)
        if tag_start == -1:
            break

        brace_start = text.find("{", tag_start + len(tag_prefix))
        if brace_start == -1:
            cursor = tag_start + len(tag_prefix)
            continue

        obj = extract_json_object(text, brace_start)
        if not obj:
            cursor = tag_start + len(tag_prefix)
            continue

        results.append(obj)
        cursor = brace_start + len(obj)

    return results


def extract_loose_json_object(text: str) -> str | None:
    open_idx = text.find("{")
    while open_idx != -1:
        json_text = extract_json_object(text, open_idx)
        if json_text:
            return json_text
        open_idx = text.find("{", open_idx + 1)
    return None


def remove_tagged_json_blocks(text: str, tag: str) -> str:
    tag_prefix = f"[{tag}:"
    result = []
    cursor = 0

    while True:
        tag_start = text.lower().find(tag_prefix.lower(), cursor)
        if tag_start == -1:
            result.append(text[cursor:])
            break

        result.append(text[cursor:tag_start])
        brace_start = text.find("{", tag_start + len(tag_prefix))
        if brace_start == -1:
            cursor = tag_start + len(tag_prefix)
            continue

        obj = extract_json_object(text, brace_start)
        if not obj:
            cursor = brace_start + 1
            continue

        cursor = brace_start + len(obj)

        # remove closing bracket if present after object
        if cursor < len(text) and text[cursor] == "]":
            cursor += 1

    return "".join(result)


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
    clean_reply = remove_tagged_json_blocks(clean_reply, "PERSONA_UPDATE_JSON")
    clean_reply = remove_tagged_json_blocks(clean_reply, "BOT_PERSONA_UPDATE_JSON")
    clean_reply = MEMORY_REMOVE_PATTERN.sub("", clean_reply)
    clean_reply = STYLE_UPDATE_PATTERN.sub("", clean_reply)
    clean_reply = NAME_UPDATE_PATTERN.sub("", clean_reply)
    clean_reply = SKIP_PATTERN.sub("", clean_reply)

    # Remove any leftover lines containing technical tags.
    clean_reply = re.sub(
        r"(?mi)^.*(MEMORY_UPDATE|MEMORY_REMOVE|STYLE_UPDATE|NAME_UPDATE|PERSONA_UPDATE_JSON|BOT_PERSONA_UPDATE_JSON|SKIP).*$",
        "",
        clean_reply,
    )
    clean_reply = re.sub(r"\n{3,}", "\n\n", clean_reply).strip()
    return clean_reply


def looks_like_persona_patch(patch: dict) -> bool:
    if not isinstance(patch, dict):
        return False
    persona_keys = {
        "traits",
        "alias",
        "archetype",
        "notes",
        "expertise_cluster",
        "behavioral_patterns",
        "loyalty_metrics",
        "vulnerabilities",
        "user_id",
        "user*id",
        "primary",
        "secondary",
    }
    return bool(set(patch.keys()) & persona_keys)


def normalize_persona_patch(patch: dict) -> dict:
    def normalize_string(value: str) -> str:
        return value.replace("\n", " ").strip()

    def normalize_keyword(value: str) -> str:
        return normalize_string(value).replace(" ", "_").lower()

    def normalize_object(value):
        if isinstance(value, dict):
            return {k: normalize_object(v) for k, v in value.items() if normalize_object(v) is not None}
        if isinstance(value, list):
            normalized_list = [normalize_object(v) for v in value]
            return [v for v in normalized_list if v is not None]
        if isinstance(value, str):
            return normalize_string(value)
        return value

    normalized = normalize_object(patch)
    if not isinstance(normalized, dict):
        return patch

    if "traits" in normalized and isinstance(normalized["traits"], list):
        normalized["traits"] = [normalize_keyword(item) for item in normalized["traits"] if isinstance(item, str)]

    if "expertise_cluster" in normalized and isinstance(normalized["expertise_cluster"], dict):
        for section in ("primary", "secondary"):
            if section in normalized["expertise_cluster"] and isinstance(normalized["expertise_cluster"][section], list):
                normalized["expertise_cluster"][section] = [
                    normalize_keyword(item)
                    for item in normalized["expertise_cluster"][section]
                    if isinstance(item, str)
                ]

    return normalized





async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming messages with custom nickname awareness and global context."""
    if not update.message:
        return

    chat_id = update.message.chat_id
    user = update.effective_user
    text = update.message.text or update.message.caption or ""
    if not text:
        text = get_message_content(update)

    # 1. Passive logging & User update (save ALL messages, regardless of age)
    await db_service.upsert_user(user.id, user.username or "", user.first_name)
    await db_service.save_message(chat_id, user.id, "user", text)

    # Direct JSON persona update from the user's message.
    # If the user sends a raw JSON object as the entire message, apply it directly (fast path).
    raw_user_json = extract_loose_json_object(text)
    if raw_user_json:
        try:
            patch = json.loads(raw_user_json)
            message_is_json_only = text.strip().startswith("{") and text.strip().endswith("}")
            if looks_like_persona_patch(patch) and message_is_json_only:
                normalized_patch = normalize_persona_patch(patch)
                await db_service.update_persona_fields(user.id, normalized_patch)
                logging.info(f"Applied direct user JSON persona patch for {user.id}: {normalized_patch}")
                await update.message.reply_text("Персона оновлена.")
                return
        except Exception as e:
            logging.debug(f"Raw user JSON persona patch parse failed: {e}")

    # 2. Check if message is too old to respond to
    message_date = update.message.date
    if message_date:
        now = datetime.now(timezone.utc)
        if message_date.tzinfo is None:
            message_date = message_date.replace(tzinfo=timezone.utc)
        age_seconds = (now - message_date).total_seconds()
        if age_seconds > settings.MAX_MESSAGE_AGE_SECONDS:
            logging.info(
                f"Message too old ({age_seconds:.0f}s) to respond to in chat {chat_id}, but saved to history"
            )
            return

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

    # 4. Extract reply-to context (the message user replied to)
    reply_context = None
    reply_msg = update.message.reply_to_message
    if reply_msg and reply_msg.from_user and reply_msg.from_user.id != context.bot.id:
        reply_text = reply_msg.text or reply_msg.caption or ""
        if reply_text:
            reply_author = reply_msg.from_user.first_name or "Користувач"
            reply_username = reply_msg.from_user.username or "unknown"
            reply_context = f"[Повідомлення, на яке відповідає користувач — від {reply_author} @{reply_username}]: {reply_text}"

    # 5. GLOBAL Context Gathering
    history = await db_service.get_recent_messages(chat_id, settings.HISTORY_CONTEXT_MAX_ITEMS)
    participants = await db_service.get_chat_participants(chat_id)
    personas_context = build_personas_context(participants)
    bot_persona_data = await db_service.get_chat_bot_persona(chat_id)
    if not bot_persona_data and chat_settings and chat_settings.bot_persona:
        bot_persona_data = {"notes": chat_settings.bot_persona}

    bot_persona_context = json.dumps(bot_persona_data or {}, ensure_ascii=False, indent=2)
    bot_style = " ".join(
        filter(None, [
            bot_persona_data.get("alias") if isinstance(bot_persona_data, dict) else None,
            bot_persona_data.get("archetype") if isinstance(bot_persona_data, dict) else None,
            bot_persona_data.get("notes") if isinstance(bot_persona_data, dict) else None,
        ])
    ).strip() or (chat_settings.bot_persona if chat_settings and chat_settings.bot_persona else "Звичайна, дружня людина, частина компанії.")
    display_name = bot_nickname or "Бот (ім'я ще не встановлено)"
    # Pass spontaneous flag so the system prompt can adjust behavior dynamically
    current_system_prompt = build_system_prompt(
        display_name,
        bot_style,
        personas_context,
        bot_persona_context,
        spontaneous=is_spontaneous,
    )
    messages = build_messages(history, current_system_prompt, reply_context=reply_context, spontaneous=is_spontaneous)

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
        # First, look for structured JSON persona updates
        json_matches = find_tagged_json_objects(response_text, "PERSONA_UPDATE_JSON")
        for json_blob in json_matches:
            try:
                import json as _json
                patch = _json.loads(json_blob)
                await db_service.update_persona_fields(persona_target_id, patch)
                logging.info(f"Applied JSON persona patch for user {persona_target_id}: {patch}")
            except Exception as e:
                logging.warning(f"Failed to apply JSON persona patch: {e}")

        # Handle legacy MEMORY_UPDATE tags (try to parse as JSON, else save as notes)
        memory_pattern = r"\[MEMORY_UPDATE:\s*(.*?)\]"
        user_updates = re.findall(memory_pattern, response_text)
        for fact in user_updates:
            fact = fact.replace("\\n", " ").replace("\n", " ").strip()
            if not fact:
                continue
            # If it looks like JSON, parse and apply
            if fact.startswith("{") and fact.endswith("}"):
                try:
                    patch = json.loads(fact)
                    await db_service.update_persona_fields(persona_target_id, patch)
                    logging.info(f"Applied JSON persona patch from MEMORY_UPDATE for user {persona_target_id}")
                    continue
                except Exception:
                    pass

            # Plain text fact — save directly as a notes entry
            await db_service.update_persona_fields(persona_target_id, {"notes": fact})
            logging.info(f"Saved MEMORY_UPDATE fact to notes for user {persona_target_id}: {fact}")

        # 1.5 Look for bot persona JSON updates
        bot_persona_matches = find_tagged_json_objects(response_text, "BOT_PERSONA_UPDATE_JSON")
        for json_blob in bot_persona_matches:
            try:
                import json as _json
                patch = _json.loads(json_blob)
                await db_service.update_chat_bot_persona_fields(chat_id, patch)
                logging.info(f"Applied JSON bot persona patch in chat {chat_id}: {patch}")
            except Exception as e:
                logging.warning(f"Failed to apply bot persona patch: {e}")

        # If the model returned a raw JSON object without explicit tags, try to interpret it
        # as a persona patch and store it for the target user.
        if not json_matches and not user_updates:
            raw_json = extract_loose_json_object(response_text)
            if raw_json:
                try:
                    loose_patch = json.loads(raw_json)
                    if looks_like_persona_patch(loose_patch):
                        await db_service.update_persona_fields(persona_target_id, loose_patch)
                        logging.info(
                            f"Applied loose JSON persona patch for user {persona_target_id}: {loose_patch}"
                        )
                except Exception as e:
                    logging.debug(f"Loose JSON persona patch parse failed: {e}")

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
            await update.message.reply_text(
                clean_reply,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            
    except Exception as e:
        logging.error(f"AI Error: {e}")
        if notify_on_error:
            await update.message.reply_text("Помилка зв'язку з мізками... (AI Error)")
