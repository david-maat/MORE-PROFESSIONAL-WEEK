import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from meshcore import EventType, MeshCore


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required env var: {name}")
    return value


async def main() -> None:
    load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

    serial_port = os.getenv("MESHCORE_SERIAL_PORT", "/dev/ttyACM0")
    channel_index = int(os.getenv("MESHCORE_CHANNEL_INDEX", "0"))
    channel_name = os.getenv("MESHCORE_CHANNEL_NAME", "web-channel")
    channel_secret_hex = _required_env("MESHCORE_CHANNEL_SECRET_HEX")
    message = os.getenv("MESHCORE_MESSAGE", "Hello from Python channel sender!")

    channel_secret = bytes.fromhex(channel_secret_hex)
    if len(channel_secret) != 16:
        raise ValueError("MESHCORE_CHANNEL_SECRET_HEX must be 32 hex chars (16 bytes)")

    meshcore = await MeshCore.create_serial(serial_port)
    try:
        # Ensure local device channel config matches your web-created channel secret.
        set_channel_result = await meshcore.commands.set_channel(
            channel_index,
            channel_name,
            channel_secret,
        )
        if set_channel_result.type == EventType.ERROR:
            print(f"Error configuring channel: {set_channel_result.payload}")
            return

        send_result = await meshcore.commands.send_chan_msg(channel_index, message)
        if send_result.type == EventType.ERROR:
            print(f"Error sending channel message: {send_result.payload}")
            return

        print("Channel message sent successfully!")
    finally:
        await meshcore.disconnect()


if __name__ == "__main__":
    asyncio.run(main())