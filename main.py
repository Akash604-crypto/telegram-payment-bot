import asyncio
from bot import main as payment_main
from help_bot import main as help_main

async def run_bots():
    await asyncio.gather(
        asyncio.to_thread(payment_main),
        asyncio.to_thread(help_main)
    )

if __name__ == "__main__":
    asyncio.run(run_bots())
