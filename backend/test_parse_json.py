import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings
from app.models.models import Candidate

engine = create_async_engine(settings.DATABASE_URL)
async_session_factory = async_sessionmaker(engine)

async def check():
    async with async_session_factory() as db:
        pass
        
if __name__ == "__main__":
    pass
