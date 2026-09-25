"""Clients for the root-side helpers: one JSON line out, one JSON line back.

deploy/control/hermes-hub-control restarts services and reads their journals;
deploy/vault/hermes-hub-vault reads and writes the Obsidian vault. Each decides
what is allowed; the hub only asks.
"""
import asyncio
import json
import os

from . import config


class ControlError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


async def call(socket_path, payload, timeout, what, limit=2 ** 20):
    """The helper's answer as a dict, whether it said yes or no."""
    if not os.path.exists(socket_path):
        raise ControlError(f"{what} non è installato sul server ({os.path.basename(socket_path)}): "
                           "lancia deploy/deploy.sh.", status=503)
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(socket_path, limit=limit), timeout)
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
        writer.close()
    except (OSError, ValueError, asyncio.TimeoutError) as e:
        raise ControlError(f"{what} non raggiungibile: {e or 'timeout'}", status=503) from e
    try:
        return json.loads(line)
    except ValueError as e:
        raise ControlError(f"risposta non valida da: {what}") from e


async def request(action, unit, timeout=20, **fields):
    answer = await call(config.CONTROL_SOCKET, {"action": action, "unit": unit, **fields}, timeout,
                        "Il controllo dei servizi")
    if not answer.get("ok"):
        raise ControlError(answer.get("error") or f"{action} {unit} non riuscito",
                           status=403 if "not allowed" in str(answer.get("error")) else 502)
    return answer
