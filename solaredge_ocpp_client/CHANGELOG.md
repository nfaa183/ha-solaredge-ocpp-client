# Changelog

## 0.2.0

- Capture mode now uses TLS: the charger only sends its credentials when "Secure Central System Connection" is on. The app creates a local root CA (to install on the charger) and ECDSA and RSA server certificates.
- New option `server_address`.
- The charger's HTTP HEAD probes no longer fill the log with errors.

## 0.1.0

- Capture mode: relays the charger's OCPP 1.6J connection to SolarEdge, records every frame and stores the charger's credentials once SolarEdge accepts them.
