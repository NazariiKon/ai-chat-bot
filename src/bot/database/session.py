from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from bot.config import settings
from bot.database.models import Base

# Create async engine
engine = create_async_engine(settings.DATABASE_URL, echo=False)

# Create async session factory
async_session = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    """Initializes the database, creating all tables."""
    async with engine.begin() as conn:
        # In production, you'd use Alembic. For now, we'll create tables manually.
        await conn.run_sync(Base.metadata.create_all)

async def get_db():
    """Dependency for getting async session."""
    async with async_session() as session:
        yield session
