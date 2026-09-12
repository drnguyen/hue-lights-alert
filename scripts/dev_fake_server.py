"""Run the service against an in-memory fake bridge, for UI work without real Hue hardware.

    python scripts/dev_fake_server.py                 # https://127.0.0.1:8765/admin, token "dev"
    TLS_ENABLED=false python scripts/dev_fake_server.py

Discovery always finds one bridge, pairing succeeds on the third poll (so the
"press the link button" state is visible), and lights are the three from tests/conftest.py.
"""
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]

import uvicorn  # noqa: E402
from conftest import FakeHueClient, FakePairing  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.storage import Store  # noqa: E402
from app.tls import ensure_certificate  # noqa: E402

logging.basicConfig(level="INFO")
env = dict(os.environ)
env.setdefault("ADMIN_TOKEN", "dev")
env.setdefault("DATA_DIR", os.path.join(ROOT, "data", "dev-fake"))
env.setdefault("PORT", "8765")
settings = load_settings(env)

fake = FakeHueClient()
pairing = FakePairing()
pairing.succeed_after = 3
app = create_app(
    settings=settings, store=Store(settings.config_path), client_factory=lambda bridge: fake,
    discover_bridges=lambda timeout: [{"ip": "10.0.0.5", "id": "001788fffe123456", "name": "Philips hue", "source": "mdns"}],
    pair_bridge=pairing,
)
tls = ensure_certificate(settings)
kwargs = {"ssl_certfile": tls[0], "ssl_keyfile": tls[1]} if tls else {}
print(f"Admin page: {settings.scheme}://127.0.0.1:{settings.port}/admin  (token: {settings.admin_token})")
uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level="info", **kwargs)
