"""Client for the root-side helper that restarts services and reads their journals.

See deploy/control/hermes-hub-control: the helper decides what is allowed;
this only asks.
"""
import asyncio
import json
import os

from . import config


class ControlError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


async def request(action, unit, timeout=20, **fields):
    if not os.path.exists(config.CONTROL_SOCKET):
        raise ControlError("Il controllo dei servizi non è installato sul server "
                           "(hermes-hub-control.socket): lancia deploy/deploy.sh.", status=503)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(config.CONTROL_SOCKET, limit=2 ** 20), timeout)
        writer.write((json.dumps({"action": action, "unit": unit, **fields}) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
        writer.close()
    except (OSError, asyncio.TimeoutError) as e:
        raise ControlError(f"controllo servizi non raggiungibile: {e or 'timeout'}", status=503) from e
    try:
        answer = json.loads(line)
    except ValueError as e:
        raise ControlError("risposta non valida dal controllo servizi") from e
    if not answer.get("ok"):
        raise ControlError(answer.get("error") or f"{action} {unit} non riuscito",
                           status=403 if "not allowed" in str(answer.get("error")) else 502)
    return answer
