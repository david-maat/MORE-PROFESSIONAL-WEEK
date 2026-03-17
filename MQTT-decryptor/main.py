import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import paho.mqtt.client as mqtt

from meshcore.meshcore_parser import MeshcorePacketParser


def _load_env() -> None:
	here = Path(__file__).resolve().parent
	# Load local overrides first, then LoRa defaults if present.
	load_dotenv(here / ".env", override=False)
	load_dotenv(here.parent / "LoRa" / ".env", override=False)


def _decode_text_payload(value: str) -> bytes | None:
	value = value.strip()
	if not value:
		return None

	# Hex string (possibly with 0x prefix).
	try:
		hex_value = value[2:] if value.lower().startswith("0x") else value
		if len(hex_value) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in hex_value):
			return bytes.fromhex(hex_value)
	except ValueError:
		pass

	# Base64 string.
	try:
		return base64.b64decode(value, validate=True)
	except Exception:
		return None


def _extract_candidate_bytes(payload: bytes) -> list[bytes]:
	candidates: list[bytes] = [payload]

	try:
		text = payload.decode("utf-8").strip()
	except UnicodeDecodeError:
		return candidates

	# Payload may be plain hex/base64 text.
	decoded = _decode_text_payload(text)
	if decoded:
		candidates.append(decoded)

	# Payload may be JSON wrapper produced by another client.
	try:
		obj = json.loads(text)
	except json.JSONDecodeError:
		return candidates

	if isinstance(obj, dict):
		for key in ("payload", "data", "raw", "raw_hex", "packet", "packet_hex", "bytes", "message"):
			val = obj.get(key)
			if isinstance(val, str):
				b = _decode_text_payload(val)
				if b:
					candidates.append(b)
			elif isinstance(val, list) and all(isinstance(x, int) and 0 <= x <= 255 for x in val):
				candidates.append(bytes(val))

	# Deduplicate preserving order.
	unique: list[bytes] = []
	seen: set[bytes] = set()
	for item in candidates:
		if item not in seen:
			seen.add(item)
			unique.append(item)
	return unique


def _build_parser(channel_name: str, channel_secret_hex: str) -> MeshcorePacketParser:
	channel_secret = bytes.fromhex(channel_secret_hex)
	if len(channel_secret) != 16:
		raise ValueError("MESHCORE_CHANNEL_SECRET_HEX must be exactly 32 hex chars")

	parser = MeshcorePacketParser()
	parser.decrypt_channels = True

	channel_hash = channel_secret.hex()
	# Real channel hash used in packets is SHA256(secret)[:2].
	from Crypto.Hash import SHA256

	channel_hash = SHA256.new(channel_secret).hexdigest()[:2]
	asyncio.run(
		parser.newChannel(
			{
				"channel_idx": 0,
				"channel_name": channel_name,
				"channel_secret": channel_secret,
				"channel_hash": channel_hash,
			}
		)
	)
	return parser


def _try_parse_candidate(parser: MeshcorePacketParser, packet: bytes) -> dict[str, Any] | None:
	if not packet:
		return None

	# If full MeshCore LOG_DATA frame (0x88), skip [type, snr, rssi].
	if packet[0] == 0x88 and len(packet) > 3:
		payload = packet[3:]
	else:
		# Already RF payload.
		payload = packet

	try:
		result = asyncio.run(parser.parsePacketPayload(payload, {}))
	except Exception:
		return None

	if "payload_type" not in result:
		return None
	return result


def main() -> None:
	_load_env()

	broker = os.getenv("MQTT_BROKER", "broker.hivemq.com")
	port = int(os.getenv("MQTT_PORT", "1883"))
	topic = os.getenv("MQTT_TOPIC", "tm/dev1/packets")
	keepalive = int(os.getenv("MQTT_KEEPALIVE", "60"))

	channel_name = os.getenv("MESHCORE_CHANNEL_NAME", "MORE-PROF-WEEK")
	channel_secret_hex = os.getenv("MESHCORE_CHANNEL_SECRET_HEX", "")
	target_channel_hash = os.getenv("MESHCORE_TARGET_CHANNEL_HASH", "4a").lower()

	if not channel_secret_hex:
		raise ValueError("Missing MESHCORE_CHANNEL_SECRET_HEX (set in LoRa/.env or MQTT decryptor/.env)")

	parser = _build_parser(channel_name, channel_secret_hex)

	def on_connect(client: mqtt.Client, _userdata: Any, _flags: dict[str, Any], rc: int, _properties: Any = None) -> None:
		if rc == 0:
			print(f"Connected to MQTT broker {broker}:{port}")
			client.subscribe(topic)
			print(f"Subscribed to topic: {topic}")
			print(f"Decrypt filter channel hash: {target_channel_hash}")
		else:
			print(f"MQTT connect failed with rc={rc}")

	def on_message(_client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
		for candidate in _extract_candidate_bytes(msg.payload):
			parsed = _try_parse_candidate(parser, candidate)
			if not parsed:
				continue

			if parsed.get("payload_type") != 0x05:
				continue

			chan_hash = str(parsed.get("chan_hash", "")).lower()
			if chan_hash != target_channel_hash:
				continue

			text = parsed.get("message")
			if text:
				print(f"[{msg.topic}] hash={chan_hash} message={text}")
			else:
				print(
					f"[{msg.topic}] hash={chan_hash} encrypted={parsed.get('crypted', '')} "
					f"(channel matched, but message not decrypted)"
				)
			return

	client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
	client.on_connect = on_connect
	client.on_message = on_message

	client.connect(broker, port, keepalive)
	client.loop_forever()


if __name__ == "__main__":
	main()
