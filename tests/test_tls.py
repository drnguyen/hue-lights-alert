import os
import stat
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from app.config import ConfigError, Settings
from app.tls import certificate_info, ensure_certificate, generate_self_signed


def _settings(tmp_path, **kw):
    base = dict(admin_token="a", session_secret="s", data_dir=str(tmp_path), tls_enabled=True,
                tls_cert_file=str(tmp_path / "certs" / "server.crt"), tls_key_file=str(tmp_path / "certs" / "server.key"),
                tls_hostnames=("localhost", "127.0.0.1", "hue.local", "::1"), tls_cert_days=100)
    base.update(kw)
    return Settings(**base)


def test_generate_self_signed_contents(tmp_path):
    cert_path, key_path = str(tmp_path / "c" / "server.crt"), str(tmp_path / "c" / "server.key")
    info = generate_self_signed(cert_path, key_path, ["localhost", " hue.local ", "127.0.0.1", "::1", "", "LOCALHOST"], days=100)

    cert = x509.load_pem_x509_certificate(open(cert_path, "rb").read())
    key = serialization.load_pem_private_key(open(key_path, "rb").read(), password=None)
    assert cert.issuer == cert.subject
    assert cert.public_key().public_numbers() == key.public_key().public_numbers()
    cert.verify_directly_issued_by(cert)  # signature is valid with its own key

    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["localhost", "hue.local"]
    assert [str(ip) for ip in san.get_values_for_type(x509.IPAddress)] == ["127.0.0.1", "::1"]
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert x509.oid.ExtendedKeyUsageOID.SERVER_AUTH in eku

    now = datetime.now(timezone.utc)
    assert cert.not_valid_before_utc <= now
    assert abs((cert.not_valid_after_utc - now) - timedelta(days=100)) < timedelta(minutes=10)

    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600
    assert info["subject"] == "hue-lights" and info["self_signed"] is True
    assert info["sans"] == ["localhost", "hue.local", "127.0.0.1", "::1"]
    assert info["days_remaining"] in (99, 100)
    assert info == certificate_info(cert_path)


def test_ensure_certificate_generates_once(tmp_path):
    s = _settings(tmp_path)
    assert ensure_certificate(s) == (s.tls_cert_file, s.tls_key_file)
    first = certificate_info(s.tls_cert_file)
    assert ensure_certificate(s) == (s.tls_cert_file, s.tls_key_file)
    assert certificate_info(s.tls_cert_file)["fingerprint_sha256"] == first["fingerprint_sha256"]
    assert set(first["sans"]) == {"localhost", "hue.local", "127.0.0.1", "::1"}


def test_ensure_certificate_regenerates_when_forced(tmp_path):
    s = _settings(tmp_path)
    ensure_certificate(s)
    first = certificate_info(s.tls_cert_file)["fingerprint_sha256"]
    ensure_certificate(_settings(tmp_path, tls_regenerate=True, tls_hostnames=("only.local",)))
    info = certificate_info(s.tls_cert_file)
    assert info["fingerprint_sha256"] != first and info["sans"] == ["only.local"]


def test_ensure_certificate_disabled(tmp_path):
    assert ensure_certificate(_settings(tmp_path, tls_enabled=False)) is None
    assert not os.path.exists(tmp_path / "certs")


def test_custom_paths_are_never_generated(tmp_path):
    s = _settings(tmp_path, tls_custom_paths=True)
    with pytest.raises(ConfigError, match="missing"):
        ensure_certificate(s)
    generate_self_signed(s.tls_cert_file, s.tls_key_file, ["x"], days=1)
    fp = certificate_info(s.tls_cert_file)["fingerprint_sha256"]
    assert ensure_certificate(_settings(tmp_path, tls_custom_paths=True, tls_regenerate=True)) == (s.tls_cert_file, s.tls_key_file)
    assert certificate_info(s.tls_cert_file)["fingerprint_sha256"] == fp  # untouched
