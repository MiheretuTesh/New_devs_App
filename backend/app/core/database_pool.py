import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import logging
from ..config import settings

logger = logging.getLogger(__name__)


def _async_database_url(url: str) -> str:
    """Normalise a libpq-style URL to the asyncpg driver used by the async engine."""
    for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
        if url.startswith(prefix):
            return url
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


class DatabasePool:
    def __init__(self):
        self.engine = None
        self.session_factory = None

    async def initialize(self):
        """Initialize database connection pool"""
        if self.session_factory:
            return

        try:
            # Build the engine from the configured DATABASE_URL. The previous
            # implementation referenced settings.supabase_db_* attributes that do not
            # exist on Settings, so initialize() raised AttributeError on every call,
            # leaving session_factory as None and silently pushing every revenue query
            # into the mock-data fallback.
            database_url = _async_database_url(settings.database_url)

            # NOTE: no explicit poolclass - create_async_engine defaults to
            # AsyncAdaptedQueuePool. The synchronous QueuePool is not compatible with
            # an async engine and raises at construction time.
            self.engine = create_async_engine(
                database_url,
                pool_size=settings.database_pool_size,
                max_overflow=settings.database_max_overflow,
                pool_pre_ping=True,  # Validate connections
                pool_recycle=settings.database_pool_recycle,
                echo=False  # Set to True for SQL debugging
            )

            self.session_factory = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            
            logger.info("✅ Database connection pool initialized")
            
        except Exception as e:
            logger.error(f"❌ Database pool initialization failed: {e}")
            self.engine = None
            self.session_factory = None
    
    async def close(self):
        """Close database connections"""
        if self.engine:
            await self.engine.dispose()
    
    def get_session(self) -> AsyncSession:
        """Get a database session from the pool.

        This is deliberately a plain (non-async) method: callers use it as
        ``async with db_pool.get_session() as session``. As an ``async def`` it
        returned a coroutine, which has no ``__aenter__``, so every such call site
        raised TypeError before a query was ever issued.
        """
        if not self.session_factory:
            raise Exception("Database pool not initialized")
        return self.session_factory()

# Global database pool instance
db_pool = DatabasePool()

async def get_db_session() -> AsyncSession:
    """Dependency to get database session"""
    async with db_pool.get_session() as session:
        yield session
