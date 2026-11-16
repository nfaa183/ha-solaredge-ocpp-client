"""OCPP 1.6J charge point client that reports to SolarEdge on the charger's behalf.

The real charger stays connected to Home Assistant's OCPP integration. This
client connects to SolarEdge as the same charge point identity and sends
BootNotification, Heartbeat, StatusNotification and MeterValues built from
Home Assistant entities. Commands from SolarEdge are answered locally and never
reach the charger.
"""

import asyncio
import base64
import json
import logging
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

import ha

log = logging.getLogger("solaredge_ocpp_client.client")

CALL = 2
CALLRESULT = 3
CALLERROR = 4
SUBPROTOCOLS = ["ocpp1.6"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Config:
    identity: str
    user: str
    password: str
    upstream_url: str
    meter_interval: int
    status_entity: str
    energy_register_entity: str  # kWh
    power_entity: str  # kW
    current_l1_entity: str
    current_l2_entity: str
    current_l3_entity: str
    current_offered_entity: str
    vendor: str
    model: str
    serial: str
    firmware: str


# Responses to commands SolarEdge may send. The charger is never touched, so a
# command that would change it is rejected; a cosmetic query is answered.
_REPORTED_CONFIG = {
    "SupportedFeatureProfiles": "Core,SmartCharging,RemoteTrigger,FirmwareManagement",
    "NumberOfConnectors": "1",
    "AuthorizeRemoteTxRequests": "false",
}


class ChargePoint:
    def __init__(self, config: Config):
        self.config = config
        self.ws = None
        self._pending: dict[str, asyncio.Future] = {}
        self.heartbeat_interval = 300
        self.meter_interval = config.meter_interval

    # ---- low-level OCPP framing -------------------------------------------------

    async def _call(self, action: str, payload: dict) -> dict:
        unique_id = str(uuid.uuid4())
        message = [CALL, unique_id, action, payload]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[unique_id] = future
        await self.ws.send(json.dumps(message))
        log.info("-> %s", action)
        try:
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(unique_id, None)

    async def _reply(self, unique_id: str, payload: dict) -> None:
        await self.ws.send(json.dumps([CALLRESULT, unique_id, payload]))

    async def _error(self, unique_id: str, code: str, description: str) -> None:
        await self.ws.send(json.dumps([CALLERROR, unique_id, code, description, {}]))

    # ---- incoming commands from SolarEdge --------------------------------------

    async def _dispatch(self, unique_id: str, action: str, payload: dict) -> None:
        handler = getattr(self, f"on_{action}", None)
        if handler is None:
            log.info("<- %s (not implemented, rejected)", action)
            await self._error(unique_id, "NotImplemented", f"{action} not supported")
            return
        log.info("<- %s", action)
        try:
            result = await handler(payload)
        except Exception as err:  # noqa: BLE001 - never drop the connection over one command
            log.error("Handler for %s failed: %r", action, err)
            await self._error(unique_id, "InternalError", "handler failed")
            return
        await self._reply(unique_id, result)

    async def on_GetConfiguration(self, payload: dict) -> dict:
        keys = payload.get("key") or list(_REPORTED_CONFIG)
        known, unknown = [], []
        for key in keys:
            if key in _REPORTED_CONFIG:
                known.append({"key": key, "readonly": True, "value": _REPORTED_CONFIG[key]})
            elif key == "HeartbeatInterval":
                known.append({"key": key, "readonly": False, "value": str(self.heartbeat_interval)})
            elif key == "MeterValueSampleInterval":
                known.append({"key": key, "readonly": False, "value": str(self.meter_interval)})
            else:
                unknown.append(key)
        return {"configurationKey": known, "unknownKey": unknown}

    async def on_ChangeConfiguration(self, payload: dict) -> dict:
        key, value = payload.get("key"), payload.get("value")
        if key == "HeartbeatInterval" and value and value.isdigit():
            self.heartbeat_interval = int(value)
            return {"status": "Accepted"}
        if key == "MeterValueSampleInterval" and value and value.isdigit():
            self.meter_interval = int(value)
            return {"status": "Accepted"}
        return {"status": "Rejected"}

    async def on_TriggerMessage(self, payload: dict) -> dict:
        requested = payload.get("requestedMessage")
        senders = {
            "BootNotification": self.send_boot_notification,
            "Heartbeat": self.send_heartbeat,
            "StatusNotification": self.send_status_notification,
            "MeterValues": self.send_meter_values,
        }
        sender = senders.get(requested)
        if sender is None:
            return {"status": "NotImplemented"}
        asyncio.create_task(self._safe(sender()))
        return {"status": "Accepted"}

    async def on_RemoteStartTransaction(self, payload: dict) -> dict:
        return {"status": "Rejected"}

    async def on_RemoteStopTransaction(self, payload: dict) -> dict:
        return {"status": "Rejected"}

    async def on_Reset(self, payload: dict) -> dict:
        return {"status": "Rejected"}

    async def on_ChangeAvailability(self, payload: dict) -> dict:
        return {"status": "Rejected"}

    async def on_UnlockConnector(self, payload: dict) -> dict:
        return {"status": "NotSupported"}

    async def on_SetChargingProfile(self, payload: dict) -> dict:
        return {"status": "Rejected"}

    async def on_ClearChargingProfile(self, payload: dict) -> dict:
        return {"status": "Unknown"}

    async def on_GetCompositeSchedule(self, payload: dict) -> dict:
        return {"status": "Rejected"}

    async def on_DataTransfer(self, payload: dict) -> dict:
        return {"status": "UnknownVendorId"}

    # ---- outgoing notifications -------------------------------------------------

    async def send_boot_notification(self) -> None:
        result = await self._call(
            "BootNotification",
            {
                "chargePointVendor": self.config.vendor,
                "chargePointModel": self.config.model,
                "chargePointSerialNumber": self.config.serial,
                "chargeBoxSerialNumber": self.config.serial,
                "firmwareVersion": self.config.firmware,
            },
        )
        interval = result.get("interval")
        if isinstance(interval, int) and interval > 0:
            self.heartbeat_interval = interval
        log.info("BootNotification: %s, heartbeat every %ss", result.get("status"), self.heartbeat_interval)

    async def send_heartbeat(self) -> None:
        await self._call("Heartbeat", {})

    async def send_status_notification(self) -> None:
        status = await ha.get_text(self.config.status_entity) or "Available"
        status = _OCPP_STATUSES.get(status.lower(), status)
        await self._call(
            "StatusNotification",
            {"connectorId": 1, "errorCode": "NoError", "status": status, "timestamp": now_iso()},
        )

    async def send_meter_values(self) -> None:
        samples = []

        async def sample(entity, measurand, unit, scale=1.0, phase=None):
            value = await ha.get_float(entity, scale)
            if value is None:
                return
            item = {
                "value": f"{value:.1f}",
                "context": "Sample.Periodic",
                "format": "Raw",
                "measurand": measurand,
                "location": "Outlet",
                "unit": unit,
            }
            if phase:
                item["phase"] = phase
            samples.append(item)

        await sample(self.config.energy_register_entity, "Energy.Active.Import.Register", "Wh", 1000.0)
        await sample(self.config.power_entity, "Power.Active.Import", "W", 1000.0)
        await sample(self.config.current_l1_entity, "Current.Import", "A", phase="L1")
        await sample(self.config.current_l2_entity, "Current.Import", "A", phase="L2")
        await sample(self.config.current_l3_entity, "Current.Import", "A", phase="L3")
        await sample(self.config.current_offered_entity, "Current.Offered", "A")

        if not samples:
            log.warning("No meter values available from Home Assistant; skipping")
            return
        await self._call(
            "MeterValues",
            {"connectorId": 1, "meterValue": [{"timestamp": now_iso(), "sampledValue": samples}]},
        )

    # ---- loops ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            await self._safe(self.send_heartbeat())

    async def _meter_loop(self) -> None:
        last_status = None
        while True:
            await self._safe(self.send_meter_values())
            status = await ha.get_text(self.config.status_entity)
            if status != last_status:
                last_status = status
                await self._safe(self.send_status_notification())
            await asyncio.sleep(self.meter_interval)

    async def _reader(self) -> None:
        async for raw in self.ws:
            try:
                message = json.loads(raw)
            except ValueError:
                log.warning("Non-JSON frame from SolarEdge: %r", raw[:120])
                continue
            kind = message[0]
            if kind == CALL:
                _, unique_id, action, payload = message
                await self._dispatch(unique_id, action, payload or {})
            elif kind == CALLRESULT:
                _, unique_id, payload = message
                future = self._pending.get(unique_id)
                if future and not future.done():
                    future.set_result(payload)
            elif kind == CALLERROR:
                _, unique_id, code = message[0:3] if len(message) >= 3 else (None, None, "")
                future = self._pending.get(message[1])
                if future and not future.done():
                    future.set_exception(RuntimeError(f"CALLERROR {code}"))

    async def _safe(self, coro) -> None:
        try:
            await coro
        except ConnectionClosed:
            raise
        except Exception as err:  # noqa: BLE001
            log.error("Send failed: %r", err)

    async def run_once(self) -> None:
        url = f"{self.config.upstream_url.rstrip('/')}/{self.config.identity}"
        token = base64.b64encode(f"{self.config.user}:{self.config.password}".encode()).decode()
        headers = {"Authorization": f"Basic {token}"}
        ssl_ctx = ssl.create_default_context() if url.startswith("wss://") else None
        async with connect(
            url, subprotocols=SUBPROTOCOLS, additional_headers=headers, ssl=ssl_ctx, open_timeout=30
        ) as ws:
            self.ws = ws
            log.info("Connected to SolarEdge as %s", self.config.identity)
            reader = asyncio.create_task(self._reader())
            await self.send_boot_notification()
            await self.send_status_notification()
            loops = [
                asyncio.create_task(self._heartbeat_loop()),
                asyncio.create_task(self._meter_loop()),
            ]
            try:
                await reader
            finally:
                for task in loops:
                    task.cancel()


_OCPP_STATUSES = {
    "available": "Available",
    "preparing": "Preparing",
    "charging": "Charging",
    "suspendedev": "SuspendedEV",
    "suspendedevse": "SuspendedEVSE",
    "finishing": "Finishing",
    "reserved": "Reserved",
    "unavailable": "Unavailable",
    "faulted": "Faulted",
}


async def run(config: Config) -> None:
    cp = ChargePoint(config)
    backoff = 5
    while True:
        try:
            await cp.run_once()
            backoff = 5
        except ConnectionClosed as err:
            log.warning("Connection to SolarEdge closed (%s); reconnecting in %ss", err, backoff)
        except Exception as err:  # noqa: BLE001
            log.error("Client error: %r; reconnecting in %ss", err, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 120)
