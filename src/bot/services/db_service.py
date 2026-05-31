import re
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from bot.database.models import User, Message, ChatSettings
from bot.database.session import async_session
from datetime import datetime

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

    async def update_bot_persona(self, chat_id: int, style_info: str):
        """Sets/Overrides the bot's style in the chat."""
        async with async_session() as session:
            async with session.begin():
                settings = await session.get(ChatSettings, chat_id)
                if not settings:
                    settings = ChatSettings(chat_id=chat_id, bot_persona=style_info)
                    session.add(settings)
                else:
                    # Overwrite style instead of appending to avoid role duplication
                    settings.bot_persona = style_info.strip()
            await session.commit()

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

    async def get_recent_messages(self, chat_id: int, limit: int = 20) -> list[dict]:
        """Retrieves recent messages for a specific chat to provide context to AI."""
        async with async_session() as session:
            stmt = (
                select(Message)
                .where(Message.chat_id == chat_id)
                .order_by(Message.timestamp.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            messages = result.scalars().all()
            
            # Return in correct order (chronological)
            return [
                {"role": msg.role, "content": msg.content} 
                for msg in reversed(messages)
            ]

db_service = DatabaseService()
