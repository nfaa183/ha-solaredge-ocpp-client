"""SolarEdge OCPP client for Home Assistant.

v0.1 implements the capture mode only: the charger connects to this app,
the app relays every frame to the SolarEdge CSMS unchanged, records the
exchange and stores the charger's OCPP credentials for the client mode.
"""

import asyncio
import base64
import json
import logging
import os
import ssl
from datetime import datetime, timezone

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, InvalidStatus

DATA_DIR = os.environ.get("DATA_DIR", "/data")
OPTIONS_FILE = os.path.join(DATA_DIR, "options.json")
CREDENTIALS_FILE = os.path.join(DATA_DIR, "credentials.json")
CAPTURE_DIR = os.environ.get("CAPTURE_DIR", "/share/solaredge_ocpp_client")
LISTEN_PORT = 9010
SUBPROTOCOLS = ["ocpp1.6"]

log = logging.getLogger("solaredge_ocpp_client")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_options() -> dict:
    with open(OPTIONS_FILE, encoding="utf-8") as f:
        return json.load(f)


class CaptureFile:
    """Append-only JSON Lines file, one line per event or OCPP frame."""

    def __init__(self, directory: str):
        os.makedirs(directory, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = os.path.join(directory, f"capture-{stamp}.jsonl")
        self._f = open(self.path, "a", encoding="utf-8")

    def write(self, kind: str, **fields) -> None:
        record = {"ts": now(), "kind": kind, **fields}
        self._f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


def parse_basic_auth(header: str | None) -> tuple[str, str] | None:
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    user, sep, password = decoded.partition(":")
    if not sep:
        return None
    return user, password


def save_credentials(identity: str, user: str, password: str) -> None:
    data = {
        "identity": identity,
        "user": user,
        "password": password,
        "captured_at": now(),
    }
    tmp = CREDENTIALS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CREDENTIALS_FILE)


def describe(raw: str) -> str:
    """Short human-readable summary of an OCPP-J frame for the app log."""
    try:
        msg = json.loads(raw)
    except ValueError:
        return "non-JSON frame"
    if not isinstance(msg, list) or not msg:
        return "unexpected frame"
    if msg[0] == 2 and len(msg) >= 3:
        return f"CALL {msg[2]} (id {msg[1]})"
    if msg[0] == 3:
        return f"CALLRESULT (id {msg[1]})"
    if msg[0] == 4:
        return f"CALLERROR {msg[2] if len(msg) > 2 else ''} (id {msg[1]})"
    return "unexpected frame"


async def pump(source, target, direction: str, capture: CaptureFile) -> None:
    async for raw in source:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        capture.write("frame", direction=direction, raw=raw)
        log.info("%s  %s  %s", direction, describe(raw), raw)
        await target.send(raw)


async def handle_charger(charger, upstream_base: str) -> None:
    request = charger.request
    identity = request.path.rstrip("/").rsplit("/", 1)[-1]
    peer = charger.remote_address[0] if charger.remote_address else "?"
    capture = CaptureFile(CAPTURE_DIR)
    log.info("Charger %s connected from %s, recording to %s", identity, peer, capture.path)

    auth_header = request.headers.get("Authorization")
    credentials = parse_basic_auth(auth_header)
    capture.write(
        "charger_connected",
        identity=identity,
        path=request.path,
        subprotocol=charger.subprotocol,
        headers={
            k: ("<redacted>" if k.lower() == "authorization" else v)
            for k, v in request.headers.raw_items()
        },
        basic_auth_present=credentials is not None,
    )

    if not credentials:
        log.warning("The charger sent no HTTP Basic credentials: nothing to capture")

    upstream_url = f"{upstream_base.rstrip('/')}/{identity}"
    headers = {"Authorization": auth_header} if auth_header else {}
    try:
        upstream = await connect(
            upstream_url,
            subprotocols=SUBPROTOCOLS,
            additional_headers=headers,
            ssl=ssl.create_default_context() if upstream_url.startswith("wss://") else None,
            open_timeout=20,
        )
    except InvalidStatus as err:
        status = err.response.status_code
        capture.write("upstream_rejected", url=upstream_url, status=status)
        log.error("SolarEdge rejected the connection to %s: HTTP %s", upstream_url, status)
        await charger.close(1011, "upstream rejected")
        capture.close()
        return
    except (OSError, asyncio.TimeoutError) as err:
        capture.write("upstream_error", url=upstream_url, error=repr(err))
        log.error("Cannot reach %s: %r", upstream_url, err)
        await charger.close(1011, "upstream unreachable")
        capture.close()
        return

    capture.write("upstream_connected", url=upstream_url, subprotocol=upstream.subprotocol)
    log.info("Connected to %s (subprotocol %s)", upstream_url, upstream.subprotocol)

    if credentials:
        # Saved only once SolarEdge has accepted them.
        user, password = credentials
        save_credentials(identity, user, password)
        log.info(
            "Credentials accepted by SolarEdge for user %s (password length %d), "
            "saved in the app's private storage",
            user,
            len(password),
        )

    tasks = [
        asyncio.create_task(pump(charger, upstream, "charger->solaredge", capture)),
        asyncio.create_task(pump(upstream, charger, "solaredge->charger", capture)),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, ConnectionClosed):
                log.error("Relay error: %r", exc)
    finally:
        await upstream.close()
        await charger.close()
        capture.write(
            "disconnected",
            charger_close=charger.close_code,
            upstream_close=upstream.close_code,
        )
        capture.close()
        log.info("Session closed, capture saved to %s", capture.path)


async def main() -> None:
    options = load_options()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    upstream_base = options["upstream_url"]

    async def handler(connection):
        await handle_charger(connection, upstream_base)

    async with serve(
        handler,
        "0.0.0.0",
        LISTEN_PORT,
        subprotocols=SUBPROTOCOLS,
        ping_interval=None,
    ) as server:
        log.info(
            "Capture mode: waiting for the charger on ws://<this host>:%d/ , relaying to %s",
            LISTEN_PORT,
            upstream_base,
        )
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
