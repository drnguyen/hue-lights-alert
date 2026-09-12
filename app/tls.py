"""Self-signed certificate generation and inspection for the HTTPS listener."""

from __future__ import annotations

import ipaddress
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.config import ConfigError, Settings

log = logging.getLogger(__name__)

COMMON_NAME = "hue-lights"
ORGANIZATION = "Hue Alert Lights"


def _san_entries(hostnames: Iterable[str]) -> list[x509.GeneralName]:
    entries: list[x509.GeneralName] = []
    seen: set[str] = set()
    for raw in hostnames:
        name = raw.strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            entries.append(x509.DNSName(name))
    if not entries:
        entries.append(x509.DNSName("localhost"))
    return entries


def generate_self_signed(cert_path: str, key_path: str, hostnames: Iterable[str],
                         days: int = 3650, common_name: str = COMMON_NAME) -> dict[str, Any]:
    """Write a self-signed certificate + RSA-2048 key (PEM). Returns certificate_info()."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANIZATION),
    ])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(_san_entries(hostnames)), critical=False)
        # CA:TRUE mirrors `openssl req -x509` defaults so the cert can be imported as a trust anchor.
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_encipherment=True, key_cert_sign=True,
            content_commitment=False, data_encipherment=False, key_agreement=False,
            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )

    for path in (cert_path, key_path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key_pem)
    os.chmod(key_path, 0o600)

    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    os.chmod(cert_path, 0o644)
    return certificate_info(cert_path)


def certificate_info(cert_path: str) -> dict[str, Any]:
    with open(cert_path, "rb") as fh:
        cert = x509.load_pem_x509_certificate(fh.read())
    sans: list[str] = []
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        sans = [str(v) for v in ext.get_values_for_type(x509.DNSName)]
        sans += [str(v) for v in ext.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        pass
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    not_after = cert.not_valid_after_utc
    fingerprint = cert.fingerprint(hashes.SHA256()).hex().upper()
    return {
        "path": cert_path,
        "subject": cn[0].value if cn else str(cert.subject),
        "self_signed": cert.issuer == cert.subject,
        "sans": sans,
        "not_before": cert.not_valid_before_utc.isoformat(),
        "not_after": not_after.isoformat(),
        "days_remaining": max(0, (not_after - datetime.now(timezone.utc)).days),
        "serial": format(cert.serial_number, "x"),
        "fingerprint_sha256": ":".join(fingerprint[i:i + 2] for i in range(0, len(fingerprint), 2)),
    }


def ensure_certificate(settings: Settings) -> Optional[tuple[str, str]]:
    """Make sure a cert/key pair exists when TLS is enabled.

    Returns (cert_path, key_path), or None when TLS is disabled. Generates a
    self-signed pair at the default location if missing or when TLS_REGENERATE
    is set. Custom paths (TLS_CERT_FILE/TLS_KEY_FILE) are never written to.
    """
    if not settings.tls_enabled:
        return None
    cert, key = settings.tls_cert_file, settings.tls_key_file
    exists = os.path.isfile(cert) and os.path.isfile(key)

    if settings.tls_custom_paths:
        if not exists:
            raise ConfigError(
                f"TLS_CERT_FILE/TLS_KEY_FILE point to missing files: {cert}, {key}. "
                "Mount them into the container or unset both to use a generated certificate.")
        return cert, key

    if exists and not settings.tls_regenerate:
        return cert, key

    reason = "TLS_REGENERATE=true" if exists else "no certificate found"
    info = generate_self_signed(cert, key, settings.tls_hostnames, settings.tls_cert_days)
    log.info("Generated self-signed TLS certificate (%s): %s, SANs=%s, valid until %s, SHA-256 %s",
             reason, cert, ",".join(info["sans"]), info["not_after"], info["fingerprint_sha256"])
    return cert, key
