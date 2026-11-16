# Changelog

## 1.0.0

- Client mode (now the default): the add-on connects to SolarEdge as the charge point and reports BootNotification, Heartbeat, StatusNotification and MeterValues built from Home Assistant entities. The charger stays on Home Assistant; commands from SolarEdge are answered locally and never reach it.
- Capture mode kept, selected with the `mode` option.

## 0.4.0

- The charger only presents its credentials after an HTTP 401 challenge; the capture endpoint now sends `401` with `WWW-Authenticate: Basic` when a connection arrives without them.

## 0.3.0

- The local certificates now carry an organization name, which the charger requires ("Certificate does not have an organization name set"). An older certificate without one is regenerated automatically.

## 0.2.0

- Capture mode now uses TLS: the charger only sends its credentials when "Secure Central System Connection" is on. The app creates a local root CA (to install on the charger) and ECDSA and RSA server certificates.
- New option `server_address`.
- The charger's HTTP HEAD probes no longer fill the log with errors.

## 0.1.0

- Capture mode: relays the charger's OCPP 1.6J connection to SolarEdge, records every frame and stores the charger's credentials once SolarEdge accepts them.
