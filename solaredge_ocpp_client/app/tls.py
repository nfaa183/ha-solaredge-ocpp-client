"""Local certificate authority for the charger-facing TLS endpoint.

The charger only sends its HTTP Basic credentials over TLS ("Secure Central
System Connection" on). It must trust the server certificate, so the app
creates its own root CA once; the user installs that root certificate on the
charger ("Install Central System Root Certificate").
"""

import ipaddress
import json
import os
import ssl
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_NAME = "SolarEdge OCPP Client local CA"


def _write_key(path: str, key) -> None:
    data = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, 0o600)


def _write_cert(path: str, cert: x509.Certificate) -> None:
    with open(path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


def _load(cert_path: str, key_path: str):
    with open(cert_path, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    with open(key_path, "rb") as f:
        key = serialization.load_pem_private_key(f.read(), password=None)
    return cert, key


def _create_ca(directory: str):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365 * 20))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write_key(os.path.join(directory, "ca.key"), key)
    _write_cert(os.path.join(directory, "ca.pem"), cert)
    return cert, key


def _san_entry(address: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(ipaddress.ip_address(address))
    except ValueError:
        return x509.DNSName(address)


def _create_leaf(directory: str, kind: str, address: str, ca_cert, ca_key) -> None:
    if kind == "ec":
        key = ec.generate_private_key(ec.SECP256R1())
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, address)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365 * 10))
        .add_extension(x509.SubjectAlternativeName([_san_entry(address)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write_key(os.path.join(directory, f"server-{kind}.key"), key)
    _write_cert(os.path.join(directory, f"server-{kind}.pem"), cert)


def ensure_certificates(directory: str, address: str) -> str:
    """Create the CA once and the server certificates for `address`.

    Returns the CA certificate in PEM format.
    """
    os.makedirs(directory, exist_ok=True)
    ca_cert_path = os.path.join(directory, "ca.pem")
    ca_key_path = os.path.join(directory, "ca.key")
    if os.path.exists(ca_cert_path) and os.path.exists(ca_key_path):
        ca_cert, ca_key = _load(ca_cert_path, ca_key_path)
    else:
        ca_cert, ca_key = _create_ca(directory)

    state_path = os.path.join(directory, "server.json")
    state = {}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    leaves_present = all(
        os.path.exists(os.path.join(directory, f"server-{k}.{ext}"))
        for k in ("ec", "rsa")
        for ext in ("pem", "key")
    )
    if state.get("address") != address or not leaves_present:
        for kind in ("ec", "rsa"):
            _create_leaf(directory, kind, address, ca_cert, ca_key)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"address": address}, f)

    return ca_cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def server_context(directory: str) -> ssl.SSLContext:
    """TLS context offering both an ECDSA and an RSA certificate."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    for kind in ("ec", "rsa"):
        ctx.load_cert_chain(
            os.path.join(directory, f"server-{kind}.pem"),
            os.path.join(directory, f"server-{kind}.key"),
        )
    return ctx
