"""SolarEdge OCPP client for Home Assistant.

Two modes, chosen by the `mode` option:

- client (default): connect to SolarEdge as the charge point and report
  Home Assistant data, so the charger stays visible in the SolarEdge app while
  Home Assistant keeps local control.
- capture: a one-time relay used to record the charger <-> SolarEdge exchange
  and capture the charger's OCPP credentials (see DOCS.md).
"""

import asyncio
import json
import logging
import os

import capture
import client

DATA_DIR = os.environ.get("DATA_DIR", "/data")
OPTIONS_FILE = os.path.join(DATA_DIR, "options.json")
CREDENTIALS_FILE = os.path.join(DATA_DIR, "credentials.json")

log = logging.getLogger("solaredge_ocpp_client")


def load_options() -> dict:
    with open(OPTIONS_FILE, encoding="utf-8") as f:
        return json.load(f)


def build_client_config(options: dict) -> client.Config:
    try:
        with open(CREDENTIALS_FILE, encoding="utf-8") as f:
            credentials = json.load(f)
    except (OSError, ValueError):
        log.error(
            "No captured credentials in %s. Run the add-on once in capture mode first.",
            CREDENTIALS_FILE,
        )
        raise SystemExit(1)

    return client.Config(
        identity=credentials["identity"],
        user=credentials["user"],
        password=credentials["password"],
        upstream_url=options["upstream_url"],
        meter_interval=int(options.get("meter_interval", 30)),
        status_entity=options["status_entity"],
        energy_register_entity=options["energy_register_entity"],
        power_entity=options["power_entity"],
        current_l1_entity=options["current_l1_entity"],
        current_l2_entity=options["current_l2_entity"],
        current_l3_entity=options["current_l3_entity"],
        current_offered_entity=options["current_offered_entity"],
        vendor=options["charge_point_vendor"],
        model=options["charge_point_model"],
        serial=options["charge_point_serial"],
        firmware=options["charge_point_firmware"],
    )


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    options = load_options()
    mode = options.get("mode", "client")
    if mode == "capture":
        await capture.run()
    else:
        await client.run(build_client_config(options))


if __name__ == "__main__":
    asyncio.run(main())
