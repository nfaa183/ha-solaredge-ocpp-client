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
| 0.1 | Capture | One-time recording of the real charger ↔ SolarEdge exchange, and capture of the charger's OCPP credentials |
| 1.0 (planned) | Client | Reports status, sessions and meter values from Home Assistant entities to SolarEdge |

## Capture mode (0.1)

The charger sends its SolarEdge credentials as HTTP Basic authentication. Nobody
needs to know the password: the app reads it from the charger's own connection.

During the capture, the charger is connected to SolarEdge through this app, not to
Home Assistant: local control (OCPP integration, evcc) is unavailable for that time.

1. Start the app. The log shows `Capture mode: waiting for the charger`.
2. On the charger's web interface, OCPP page, change **only the port** of the central
   system from the Home Assistant OCPP port (usually `9000`) to `9010`.
   Host, path, identity, Basic authentication and *Secure* (off) stay as they are.
3. In the app log, check for:
   - `Connected to wss://csms.solaredge.com:443/gw/<identity>`
   - `Credentials accepted by SolarEdge … saved in the app's private storage`
4. Leave it running for about 15 minutes. If possible, plug in a car and let a
   session start and stop: the transaction messages are needed for the client mode.
5. Set the charger's port back to `9000`, then stop the app.

### Output

- Credentials: `/data/credentials.json`, inside the app's private storage
  (included in Home Assistant backups, never written to the log).
- Recording: `/share/solaredge_ocpp_client/capture-<date>-<time>.jsonl`,
  one JSON object per event or OCPP frame. The `Authorization` header is redacted.
  Every frame is also written to the app log.

### Troubleshooting

| Log message | Meaning |
|---|---|
| `The charger sent no HTTP Basic credentials` | Basic authentication is not enabled on the charger |
| `SolarEdge rejected the connection … HTTP 401` | Wrong credentials; nothing is saved |
| `SolarEdge rejected the connection … HTTP 404` | Wrong host, port or path in the app option |
