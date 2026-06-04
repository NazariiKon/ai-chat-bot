import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY")
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "auto")
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "")
    MODEL_NAME: str = os.getenv("MODEL_NAME") or "llama-3.3-70b-versatile"
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5")
    DATABASE_URL: str = os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///bot.db"
    MAX_MESSAGE_AGE_SECONDS: int = int(os.getenv("MAX_MESSAGE_AGE_SECONDS", "60"))
    HISTORY_CONTEXT_MAX_ITEMS: int = int(os.getenv("HISTORY_CONTEXT_MAX_ITEMS", "50"))
    HISTORY_CONTEXT_WORD_LIMIT: int = int(os.getenv("HISTORY_CONTEXT_WORD_LIMIT", "5000"))
    SPONTANEOUS_RESPONSE_CHANCE: float = float(os.getenv("SPONTANEOUS_RESPONSE_CHANCE", "0.20"))
    SPONTANEOUS_ONLY_QUESTION: bool = os.getenv("SPONTANEOUS_ONLY_QUESTION", "false").lower() in ("1", "true", "yes")

settings = Settings()
