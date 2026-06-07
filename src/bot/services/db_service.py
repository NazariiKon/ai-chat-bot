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

    async def update_chat_bot_persona_fields(self, chat_id: int, patch: dict):
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

                    def deep_merge(a: dict, b: dict):
                        result = deepcopy(a)
                        for k, v in b.items():
                            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                                result[k] = deep_merge(result[k], v)
                            else:
                                result[k] = v
                        return result

                    merged = deep_merge(current, patch)
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

        await self.update_chat_bot_persona_fields(chat_id, patch)

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

        async with async_session() as session:
            async with session.begin():
                user = await session.get(User, tg_id)
                if user:
                    current_persona = user.persona or ""
                    if clean_fact not in current_persona:
                        user.persona = f"{current_persona}\n- {clean_fact}".strip()
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

    async def update_persona_fields(self, tg_id: int, patch: dict):
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

                def deep_merge(a: dict, b: dict):
                    result = deepcopy(a)
                    for k, v in b.items():
                        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                            result[k] = deep_merge(result[k], v)
                        else:
                            result[k] = v
                    return result

                merged = deep_merge(current, patch)
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
                if user and user.persona:
                    pattern = re.compile(re.escape(clean_keywords), re.IGNORECASE)
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
