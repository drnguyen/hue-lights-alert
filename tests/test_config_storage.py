import pytest

from app.config import ConfigError, load_settings
from app.storage import Store


def test_admin_token_is_required():
    with pytest.raises(ConfigError, match="ADMIN_TOKEN"):
        load_settings({})
    with pytest.raises(ConfigError, match="ADMIN_TOKEN"):
        load_settings({"ADMIN_TOKEN": "   "})


def test_settings_from_env():
    s = load_settings({"ADMIN_TOKEN": "abc", "SESSION_TTL_HOURS": "3", "DATA_DIR": "/tmp/x"})
    assert s.admin_token == "abc"
    assert s.session_ttl_hours == 3
    assert s.config_path == "/tmp/x/config.json"
    assert len(s.session_secret) > 20  # auto-generated


def test_invalid_ttl():
    with pytest.raises(ConfigError):
        load_settings({"ADMIN_TOKEN": "abc", "SESSION_TTL_HOURS": "zero"})


def test_store_roundtrip(tmp_path):
    path = str(tmp_path / "config.json")
    store = Store(path)
    assert store.get_bridge() is None
    store.set_bridge(ip="1.2.3.4", username="u", bridge_id="id1")
    store.set_selected_lights(["10", "2", "2"])
    store.update_settings(min_brightness=20, max_brightness=200, glow_period_seconds=2.0, bogus=1)
    plaintext, record = store.create_token("ci")

    reloaded = Store(path)
    assert reloaded.get_bridge()["ip"] == "1.2.3.4"
    assert reloaded.get_bridge()["username"] == "u"
    assert reloaded.get_selected_lights() == ["2", "10"]
    assert reloaded.get_settings() == {"min_brightness": 20, "max_brightness": 200, "glow_period_seconds": 2.0}
    assert plaintext.startswith("hue_")
    assert "hash" not in record
    assert reloaded.verify_token(plaintext)["id"] == record["id"]
    assert reloaded.verify_token("hue_wrong") is None
    assert reloaded.list_tokens()[0]["last_used_at"] is not None
    assert reloaded.revoke_token(record["id"]) is True
    assert reloaded.revoke_token(record["id"]) is False
    assert reloaded.verify_token(plaintext) is None

    reloaded.clear_bridge()
    assert Store(path).get_bridge() is None
    assert Store(path).get_selected_lights() == []


def test_tls_and_port_defaults():
    s = load_settings({"ADMIN_TOKEN": "abc", "DATA_DIR": "/tmp/x"})
    assert s.tls_enabled is True and s.scheme == "https"
    assert s.tls_cert_file == "/tmp/x/certs/server.crt" and s.tls_key_file == "/tmp/x/certs/server.key"
    assert s.tls_custom_paths is False
    assert s.tls_hostnames[:2] == ("localhost", "127.0.0.1")
    assert s.tls_cert_days == 3650 and s.tls_regenerate is False
    assert s.port == 8000 and s.host == "0.0.0.0"


def test_tls_and_port_from_env():
    s = load_settings({"ADMIN_TOKEN": "abc", "TLS_ENABLED": "false", "PORT": "8443", "HOST": "127.0.0.1",
                       "TLS_HOSTNAMES": " hue.local, 192.168.1.50 ,,", "TLS_CERT_DAYS": "30", "TLS_REGENERATE": "yes",
                       "TLS_CERT_FILE": "/certs/a.crt", "TLS_KEY_FILE": "/certs/a.key"})
    assert s.tls_enabled is False and s.scheme == "http"
    assert s.port == 8443 and s.host == "127.0.0.1"
    assert s.tls_hostnames == ("hue.local", "192.168.1.50")
    assert s.tls_cert_days == 30 and s.tls_regenerate is True
    assert s.tls_custom_paths is True and s.tls_cert_file == "/certs/a.crt"


@pytest.mark.parametrize("env", [
    {"PORT": "0"}, {"PORT": "70000"}, {"PORT": "http"},
    {"TLS_ENABLED": "maybe"}, {"TLS_CERT_DAYS": "0"},
    {"TLS_CERT_FILE": "/only-cert.crt"},
])
def test_invalid_tls_and_port_values(env):
    with pytest.raises(ConfigError):
        load_settings({"ADMIN_TOKEN": "abc", **env})
