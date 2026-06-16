import asyncio
from app.core.database import AsyncSessionLocal
from app.models.notebook import Notebook
from app.utils.qr import generate_qr
from sqlalchemy import select

async def backfill():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Notebook).where(Notebook.published == True))
        for nb in result.scalars().all():
            if not nb.qr_code:
                nb.qr_code, nb.qr_url = generate_qr(str(nb.id))
        await db.commit()
        print("Done")

asyncio.run(backfill())