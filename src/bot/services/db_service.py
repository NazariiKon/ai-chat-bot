import re
from typing import List, Optional
import logging
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from bot.database.models import User, Message, ChatSettings
from bot.database.session import async_session
from datetime import datetime
import json
from copy import deepcopy

logger = logging.getLogger(__name__)

class DatabaseService:
    def _normalize_text(self, text: str) -> str:
        """Normalize freeform persona text for consistent storage.

        - Trim whitespace and surrounding quotes
        - Collapse internal whitespace
        - Lowercase
        - Strip trailing punctuation
        """
        if not isinstance(text, str):
            return text
        s = text.strip()
        s = s.strip('"\'')
        s = re.sub(r"\s+", " ", s)
        s = s.rstrip(" .,!;:")
        return s.lower()

    def _to_snake_case(self, text: str) -> str:
        """Convert arbitrary text to a stable snake_case tag.

        - Normalize whitespace and punctuation
        - Replace non-alphanumeric with underscore
        - Collapse multiple underscores
        - Strip leading/trailing underscores
        - Lowercase
        """
        if not isinstance(text, str):
            return text
        s = self._normalize_text(text)
        # replace non alnum with underscore
        s = re.sub(r"[^0-9a-z]+", "_", s)
        s = re.sub(r"_+", "_", s)
        s = s.strip("_")
        return s

    @staticmethod
    def _clean_patch_values(value):
        """Recursively remove None and blank-string values from a patch."""
        if isinstance(value, dict):
            cleaned = {}
            for k, v in value.items():
                cleaned_value = DatabaseService._clean_patch_values(v)
                if cleaned_value is not None:
                    cleaned[k] = cleaned_value
            return cleaned
        if isinstance(value, list):
            cleaned_list = [DatabaseService._clean_patch_values(v) for v in value]
            cleaned_list = [v for v in cleaned_list if v is not None]
            return cleaned_list
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def _deep_merge(self, a: dict, b: dict, override_notes: bool = False) -> dict:
        """Recursively merge dict `b` into dict `a`.

        - None values in `b` remove the key from the result.
        - Lists support removal markers (strings prefixed with "-").
        - Tag-like lists (traits, primary, secondary) are normalized to snake_case.
        - String values are replaced by default, except for 'notes' which is appended.
        - If `override_notes` is True, even 'notes' is replaced.
        """
        result = deepcopy(a)
        for k, v in b.items():
            if v is None:
                result.pop(k, None)
                continue
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = {} if not v else self._deep_merge(result[k], v, override_notes=override_notes)
            elif k in result and isinstance(result[k], list) and isinstance(v, list):
                if not v:
                    result[k] = []
                else:
                    existing = list(result[k])
                    remove_items = [item for item in v if isinstance(item, str) and item.strip().startswith("-")]
                    add_items = [item for item in v if not (isinstance(item, str) and item.strip().startswith("-"))]

                    if k in ("traits", "primary", "secondary"):
                        existing = [self._to_snake_case(self._normalize_text(e)) if isinstance(e, str) else e for e in existing]
                        remove_snakes = [self._to_snake_case(self._normalize_text(item[1:])) for item in remove_items]
                        if remove_snakes:
                            existing = [item for item in existing if self._to_snake_case(self._normalize_text(item)) not in remove_snakes]
                        for item in add_items:
                            if isinstance(item, str):
                                snake = self._to_snake_case(self._normalize_text(item))
                                if snake and not any(self._to_snake_case(self._normalize_text(e)) == snake for e in existing):
                                    existing.append(snake)
                            else:
                                if item not in existing:
                                    existing.append(item)
                    else:
                        remove_norms = [self._normalize_text(item[1:]) for item in remove_items]
                        if remove_norms:
                            existing = [item for item in existing if self._normalize_text(item) not in remove_norms]
                        for item in add_items:
                            if isinstance(item, str):
                                norm = self._normalize_text(item)
                                if not any(self._normalize_text(e) == norm for e in existing):
                                    existing.append(item)
                            else:
                                if item not in existing:
                                    existing.append(item)
                    result[k] = existing
            else:
                # String merging logic:
                # - 'notes' is appended by default (for cumulative memory).
                # - All other fields (alias, archetype, etc.) are replaced.
                # - If override_notes is True, everything is replaced.
                if k == "notes" and not override_notes and isinstance(result.get(k), str) and isinstance(v, str):
                    existing_str = result.get(k) or ""
                    incoming_str = v or ""
                    incoming_norm = self._normalize_text(incoming_str)
                    existing_norm = self._normalize_text(existing_str)
                    if incoming_norm and incoming_norm not in existing_norm:
                        combined = (existing_str + "\n" + incoming_norm).strip()
                        result[k] = combined
                    else:
                        result[k] = existing_str
                else:
                    # Default behavior for single-value fields (strings, ints, etc.)
                    result[k] = v
        return result

    async def get_user(self, tg_id: int) -> Optional[User]:
        """Returns a User object by its telegram ID."""
        async with async_session() as session:
            return await session.get(User, tg_id)

    async def get_users_by_ids(self, tg_ids: List[int]) -> List[User]:
        """Returns multiple users by their IDs."""
        async with async_session() as session:
            stmt = select(User).where(User.tg_id.in_(tg_ids))
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_chat_participants(self, chat_id: int) -> List[User]:
        """Returns all unique users who have sent messages in a specific chat."""
        async with async_session() as session:
            # Get unique user IDs from messages in this chat
            stmt = select(Message.user_id).where(Message.chat_id == chat_id).distinct()
            result = await session.execute(stmt)
            user_ids = result.scalars().all()
            
            if not user_ids:
                return []
                
            # Fetch user profiles
            user_stmt = select(User).where(User.tg_id.in_(user_ids))
            user_result = await session.execute(user_stmt)
            return list(user_result.scalars().all())

    async def get_chat_settings(self, chat_id: int) -> Optional[ChatSettings]:
        """Returns settings for a specific chat."""
        async with async_session() as session:
            return await session.get(ChatSettings, chat_id)

    async def set_chat_nickname(self, chat_id: int, nickname: str):
        """Sets the nickname for the bot in a specific chat."""
        async with async_session() as session:
            async with session.begin():
                settings = await session.get(ChatSettings, chat_id)
                if not settings:
                    settings = ChatSettings(chat_id=chat_id, bot_nickname=nickname)
                    session.add(settings)
                else:
                    settings.bot_nickname = nickname
            await session.commit()

    async def set_spontaneous_response_chance(self, chat_id: int, chance: float):
        """Sets the spontaneous response chance for the bot in a specific chat."""
        async with async_session() as session:
            async with session.begin():
                settings = await session.get(ChatSettings, chat_id)
                if not settings:
                    settings = ChatSettings(chat_id=chat_id, spontaneous_response_chance=chance)
                    session.add(settings)
                else:
                    settings.spontaneous_response_chance = chance
            await session.commit()

    async def set_bot_persona(self, chat_id: int, persona: str):
        """Sets the persona/behavior for the bot in a specific chat."""
        async with async_session() as session:
            async with session.begin():
                settings = await session.get(ChatSettings, chat_id)
                if not settings:
                    settings = ChatSettings(chat_id=chat_id, bot_persona=persona)
                    session.add(settings)
                else:
                    settings.bot_persona = persona
            await session.commit()

    async def update_chat_bot_persona_fields(self, chat_id: int, patch: dict, override_notes: bool = True):
        """Recursively merge `patch` into the bot's persona JSON."""
        if not isinstance(patch, dict):
            return

        async with async_session() as session:
            async with session.begin():
                settings = await session.get(ChatSettings, chat_id)
                if not settings:
                    settings = ChatSettings(chat_id=chat_id, bot_persona=json.dumps(patch, ensure_ascii=False))
                    session.add(settings)
                else:
                    try:
                        current = json.loads(settings.bot_persona) if settings.bot_persona else {}
                        if not isinstance(current, dict):
                            current = {"notes": str(current)}
                    except Exception:
                        current = {"notes": settings.bot_persona or ""}

                    patch = self._clean_patch_values(patch)
                    if not patch and patch != {}:
                        return

                    merged = self._deep_merge(current, patch, override_notes=override_notes)
                    settings.bot_persona = json.dumps(merged, ensure_ascii=False)
            await session.commit()

    async def update_bot_persona(self, chat_id: int, style_info: str):
        """Sets/Overrides the bot's style in the chat."""
        if not style_info:
            return

        try:
            patch = json.loads(style_info)
        except Exception:
            patch = {"notes": style_info.strip()}

        # STYLE_UPDATE is usually a redirection of the bot's identity,
        # so we override any existing notes/style info.
        await self.update_chat_bot_persona_fields(chat_id, patch, override_notes=True)

    async def clear_persona(self, tg_id: int):
        """Resets the user's persona."""
        async with async_session() as session:
            async with session.begin():
                user = await session.get(User, tg_id)
                if user:
                    user.persona = None
            await session.commit()

    async def upsert_user(self, tg_id: int, username: str, first_name: str):
        """Creates or updates a user in the database."""
        async with async_session() as session:
            async with session.begin():
                user = await session.get(User, tg_id)
                if not user:
                    user = User(tg_id=tg_id, username=username, first_name=first_name)
                    session.add(user)
                else:
                    user.username = username
                    user.first_name = first_name
                    user.message_count += 1
            await session.commit()

    async def update_persona(self, tg_id: int, new_fact: str):
        """Appends new information to the user's persona."""
        clean_fact = new_fact.strip()
        if not clean_fact:
            return
        normalized = self._normalize_text(clean_fact)
        if not normalized:
            return

        async with async_session() as session:
            async with session.begin():
                user = await session.get(User, tg_id)
                if user:
                    current_persona = user.persona or ""
                    current_norm = self._normalize_text(current_persona) if current_persona else ""
                    if normalized not in current_norm:
                        user.persona = f"{current_persona}\n- {normalized}".strip()
            await session.commit()

    # --- JSON persona helpers ---
    async def get_persona(self, tg_id: int) -> dict:
        """Return persona as a dict. If stored persona is plain text, wrap into {'notes': text}."""
        async with async_session() as session:
            user = await session.get(User, tg_id)
            if not user or not user.persona:
                return {}
            try:
                return json.loads(user.persona)
            except Exception:
                return {"notes": user.persona}

    async def set_persona(self, tg_id: int, persona_obj: dict):
        """Store persona as JSON string."""
        async with async_session() as session:
            async with session.begin():
                user = await session.get(User, tg_id)
                if user:
                    user.persona = json.dumps(persona_obj, ensure_ascii=False)
            await session.commit()

    async def update_persona_fields(self, tg_id: int, patch: dict, override_notes: bool = False):
        """Recursively merge `patch` into existing persona dict and save."""
        if not isinstance(patch, dict):
            return

        async with async_session() as session:
            async with session.begin():
                user = await session.get(User, tg_id)
                if not user:
                    return
                try:
                    current = json.loads(user.persona) if user.persona else {}
                    if not isinstance(current, dict):
                        current = {"notes": str(current)}
                except Exception:
                    current = {"notes": user.persona or ""}

                patch = self._clean_patch_values(patch)
                if not patch and patch != {}:
                    return

                merged = self._deep_merge(current, patch, override_notes=override_notes)
                user.persona = json.dumps(merged, ensure_ascii=False)
            await session.commit()

    async def get_chat_bot_persona(self, chat_id: int) -> dict:
        async with async_session() as session:
            settings = await session.get(ChatSettings, chat_id)
            if not settings or not settings.bot_persona:
                return {}
            try:
                return json.loads(settings.bot_persona)
            except Exception:
                return {"notes": settings.bot_persona}

    async def set_chat_bot_persona(self, chat_id: int, persona_obj: dict):
        async with async_session() as session:
            async with session.begin():
                settings = await session.get(ChatSettings, chat_id)
                if not settings:
                    settings = ChatSettings(chat_id=chat_id, bot_persona=json.dumps(persona_obj, ensure_ascii=False))
                    session.add(settings)
                else:
                    settings.bot_persona = json.dumps(persona_obj, ensure_ascii=False)
            await session.commit()

    async def remove_from_persona(self, tg_id: int, keywords: str):
        """Removes facts from the user's persona that contain specific keywords."""
        clean_keywords = keywords.strip()
        if not clean_keywords:
            return

        async with async_session() as session:
            async with session.begin():
                user = await session.get(User, tg_id)
                if not user or not user.persona:
                    return

                pattern = re.compile(re.escape(clean_keywords), re.IGNORECASE)
                
                # Check if it's a JSON persona or plain text
                is_json = False
                persona_data = {}
                try:
                    persona_data = json.loads(user.persona)
                    if isinstance(persona_data, dict):
                        is_json = True
                except Exception:
                    is_json = False

                if is_json:
                    # In JSON mode, we primarily search and remove from 'notes' field
                    # and potentially other string fields if they match.
                    changed = False
                    if "notes" in persona_data and isinstance(persona_data["notes"], str):
                        lines = persona_data["notes"].split("\n")
                        new_lines = [l for l in lines if not pattern.search(l)]
                        if len(new_lines) != len(lines):
                            persona_data["notes"] = "\n".join(new_lines).strip()
                            if not persona_data["notes"]:
                                del persona_data["notes"]
                            changed = True
                    
                    # Also check top-level string fields like 'alias', 'archetype'
                    for k in ["alias", "archetype"]:
                        if k in persona_data and isinstance(persona_data[k], str):
                            if pattern.search(persona_data[k]):
                                del persona_data[k]
                                changed = True
                    
                    if changed:
                        user.persona = json.dumps(persona_data, ensure_ascii=False) if persona_data else None
                else:
                    # Plain text mode
                    lines = user.persona.split('\n')
                    new_lines = [l for l in lines if not pattern.search(l)]
                    user.persona = '\n'.join(new_lines).strip() if new_lines else None
                    
            await session.commit()

    async def save_message(self, chat_id: int, user_id: int, role: str, content: str):
        """Saves a message to the database."""
        try:
            async with async_session() as session:
                async with session.begin():
                    message = Message(
                        chat_id=chat_id,
                        user_id=user_id,
                        role=role,
                        content=content,
                        timestamp=datetime.utcnow()
                    )
                    session.add(message)
                await session.commit()
            logger.debug(f"Saved message: chat={chat_id}, user={user_id}, role={role}, content_len={len(content)}")
        except Exception as e:
            logger.error(f"Failed to save message to database: {e}", exc_info=True)
            raise

    async def get_recent_messages(self, chat_id: int, limit: int = 30) -> list[dict]:
        """Retrieves recent messages for a specific chat to provide context to AI.
        
        For user messages, the content is prefixed with the sender's name and username
        so the AI model knows who said what in group conversations.
        """
        async with async_session() as session:
            stmt = (
                select(Message)
                .options(selectinload(Message.user))
                .where(Message.chat_id == chat_id)
                .order_by(Message.timestamp.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            messages = result.scalars().all()
            
            logger.debug(f"Retrieved {len(messages)} messages for chat {chat_id} (limit={limit})")
            
            # Return in correct order (chronological)
            output = []
            for msg in reversed(messages):
                content = msg.content
                if msg.role == "user" and msg.user:
                    name = msg.user.first_name or "Користувач"
                    username = msg.user.username or "unknown"
                    content = f"[{name} @{username}]: {content}"
                output.append({"role": msg.role, "content": content})
            
            logger.debug(f"Built context with {len(output)} formatted messages")
            return output

db_service = DatabaseService()
