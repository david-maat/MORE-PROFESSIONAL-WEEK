import asyncio
from meshcore import MeshCore, EventType

async def main():
    mc = await MeshCore.create_serial("/dev/ttyACM0")
    try:
        for idx in range(0, 8):  # try first 8 slots
            result = await mc.commands.get_channel(idx)
            if result.type == EventType.ERROR:
                continue
            print(f"index={idx} info={result.payload}")
    finally:
        await mc.disconnect()

asyncio.run(main())