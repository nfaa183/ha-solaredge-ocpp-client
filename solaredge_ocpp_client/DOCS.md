# SolarEdge OCPP Client

## Why

A SolarEdge ONE EV charger (KEBA KC-P30) can only talk to one OCPP central system.
When it is pointed at Home Assistant's [OCPP integration](https://github.com/lbbrhzn/ocpp)
for local control, it disappears from the SolarEdge app.

This app connects to SolarEdge **as an OCPP client on behalf of the charger**, using
data already in Home Assistant, so the charger stays visible in the SolarEdge app while
Home Assistant keeps full control. Commands sent by SolarEdge never reach the charger.

## Status

| Version | Mode | What it does |
|---|---|---|
| 0.2 | Capture | One-time recording of the real charger ↔ SolarEdge exchange, and capture of the charger's OCPP credentials |
| 1.0 (planned) | Client | Reports status, sessions and meter values from Home Assistant entities to SolarEdge |

## Capture mode

The charger sends its SolarEdge credentials (HTTP Basic authentication) **only over
TLS**. Nobody needs to know the password: the app reads it from the charger's own
connection. For that, the charger must trust the app: on first start, the app
creates a local root certificate that you install on the charger.

During the capture, the charger is connected to SolarEdge through this app, not to
Home Assistant: local control (OCPP integration, evcc) is unavailable for that time.

1. Set the option **Home Assistant address** to the address the charger uses as
   *Central System Address* (for example `192.168.1.10`), then start the app.
2. Get the root certificate: `/share/solaredge_ocpp_client/solaredge-ocpp-client-ca.pem`
   (also printed in the app log, between `BEGIN CERTIFICATE` and `END CERTIFICATE`).
3. On the charger's web interface, OCPP page:
   - **Install Central System Root Certificate** → select the `.pem` file;
   - *Central System Port*: `9010`;
   - *Secure Central System Connection*: **ON**;
   - leave address, path, identity, authentication method and key unchanged; **Apply**.
4. In the app log, check for:
   - `Connected to wss://csms.solaredge.com:443/gw/<identity>`
   - `Credentials accepted by SolarEdge … saved in the app's private storage`
5. Leave it running for about 15 minutes. If possible, plug in a car and let a
   session start and stop: the transaction messages are needed for the client mode.
6. Restore the charger: port `9000`, *Secure* **OFF**, **Apply**. Stop the app.
   The root certificate can stay on the charger.

### Output

- Credentials: `/data/credentials.json`, inside the app's private storage
  (included in Home Assistant backups, never written to the log).
- Local CA and server certificates: `/data/tls/` (private key never leaves the app).
- Recording: `/share/solaredge_ocpp_client/capture-<date>-<time>.jsonl`,
  one JSON object per event or OCPP frame. The `Authorization` header is redacted.
  Every frame is also written to the app log.

### Troubleshooting

| Log message | Meaning |
|---|---|
| `The charger sent no HTTP Basic credentials` | *Secure* is off on the charger, or Basic authentication is not enabled |
| Charger shows *Not Reachable*, nothing in the app log | The charger rejects the certificate: check the root certificate is installed and *Home Assistant address* matches the charger's *Central System Address* |
| `SolarEdge rejected the connection … HTTP 401` | Wrong credentials; nothing is saved |
| `SolarEdge rejected the connection … HTTP 404` | Wrong host, port or path in the app option |
