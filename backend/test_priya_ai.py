import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings
from app.models.models import Candidate
from app.services.llm_provider import get_llm_provider
from app.services.prompts import RESUME_PARSING_PROMPT

engine = create_async_engine(settings.DATABASE_URL)
async_session_factory = async_sessionmaker(engine)

async def test_parse():
    async with async_session_factory() as db:
        res = await db.execute(select(Candidate).where(Candidate.name == "Priya AI"))
        c = res.scalars().first()
        if not c:
            print("Candidate Priya AI not found")
            return
            
        print(f"Found candidate: {c.name}")
        provider = get_llm_provider()
        prompt = RESUME_PARSING_PROMPT.format(resume_text=c.resume_text)
        
        print(f"Calling Ollama (prompt len={len(prompt)})...")
        resp = await provider.generate(prompt=prompt)
        print(f"Ollama finished. Resp len={len(resp.content)}")
        
        # Save raw output
        with open("priya_ai_raw.json", "w") as f:
            f.write(resp.content)
            
        # Try parsing directly
        import json
        try:
            parsed = json.loads(resp.content)
            print("Parsed cleanly via json.loads!")
        except json.JSONDecodeError as e:
            print(f"json.loads FAILED at line {e.lineno}, col {e.colno}, pos {e.pos}: {e.msg}")
            
        # Try robust parsing
        parsed = resp.as_json()
        if parsed:
            print("Parsed successfully via robust extraction!")
        else:
            print("Robust extraction FAILED too.")
            
if __name__ == "__main__":
    asyncio.run(test_parse())
