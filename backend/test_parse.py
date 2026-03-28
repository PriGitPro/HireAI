import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings
from app.models.models import Candidate
from app.services.llm_provider import get_llm_provider
from app.services.prompts import RESUME_PARSING_PROMPT

engine = create_async_engine(settings.DATABASE_URL)
async_session_factory = async_sessionmaker(engine)

async def main():
    async with async_session_factory() as db:
        res = await db.execute(select(Candidate).where(Candidate.name.like("%Priya%")))
        c = res.scalars().first()
        if not c:
            print("Candidate not found")
            return
            
        print(f"Found candidate: {c.name}")
        provider = get_llm_provider()
        prompt = RESUME_PARSING_PROMPT.format(resume_text=c.resume_text)
        
        # Call Ollama directly
        print("Calling Ollama...")
        resp = await provider.generate(prompt=prompt)
        print("Ollama finished.")
        
        # Save raw output
        with open("raw_output.json", "w") as f:
            f.write(resp.content)
            
        # Try parsing
        import json
        try:
            parsed = json.loads(resp.content)
            print("Parsed cleanly.")
        except json.JSONDecodeError as e:
            print(f"JSON Decode Error at line {e.lineno}, col {e.colno}, pos {e.pos}: {e.msg}")
            
if __name__ == "__main__":
    asyncio.run(main())
