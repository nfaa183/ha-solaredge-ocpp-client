"""Read Home Assistant entity states through the Supervisor proxy.

The app runs as a Home Assistant add-on with `homeassistant_api: true`, so it
reaches the Core API at http://supervisor/core/api with the Supervisor token.
"""

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request

BASE_URL = os.environ.get("HA_BASE_URL", "http://supervisor/core/api")

log = logging.getLogger("solaredge_ocpp_client.ha")
_reported_error = False


def _load_token() -> str:
    """Find the Supervisor token.

    s6-overlay does not always export it to the add-on's CMD, so fall back to
    the container-environment files it writes.
    """
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
    if token:
        return token
    for path in (
        "/run/s6/container_environment/SUPERVISOR_TOKEN",
        "/var/run/s6/container_environment/SUPERVISOR_TOKEN",
        "/run/s6/container_environment/HASSIO_TOKEN",
    ):
        try:
            with open(path, encoding="utf-8") as f:
                value = f.read().strip()
                if value:
                    return value
        except OSError:
            continue
    return ""


TOKEN = _load_token()


def _get_state_blocking(entity_id: str) -> dict | None:
    global _reported_error
    url = f"{BASE_URL}/states/{entity_id}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as err:
        if not _reported_error:
            _reported_error = True
            log.warning(
                "Home Assistant API call failed (%s, token %s): %r",
                url,
                "set" if TOKEN else "MISSING",
                err,
            )
        return None


async def get_state(entity_id: str) -> dict | None:
    """Return the full state object for an entity, or None if unavailable."""
    return await asyncio.to_thread(_get_state_blocking, entity_id)


_UNKNOWN = {"", "unknown", "unavailable", "none", None}


async def get_float(entity_id: str, scale: float = 1.0) -> float | None:
    """Return an entity's numeric state multiplied by `scale`, or None."""
    state = await get_state(entity_id)
    if not state:
        return None
    value = state.get("state")
    if value is None or str(value).lower() in _UNKNOWN:
        return None
    try:
        return float(value) * scale
    except (TypeError, ValueError):
        return None


async def get_text(entity_id: str) -> str | None:
    """Return an entity's state as text, or None if unknown/unavailable."""
    state = await get_state(entity_id)
    if not state:
        return None
    value = state.get("state")
    if value is None or str(value).lower() in _UNKNOWN:
        return None
    return str(value)
