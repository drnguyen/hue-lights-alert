# Hue Alert Lights

A small self-hosted service that turns selected Philips Hue lights into alert
indicators. It has an admin page for pairing the bridge, choosing lights and
managing API tokens, and a token-protected HTTP API other applications call to
raise **Amber** or **Red** alerts in **glow** (pulsing) or **dead** (static) mode.

Built with [`huesdk`](https://pypi.org/project/huesdk/) and FastAPI.

## Quick start (Docker)

```bash
cp .env.example .env          # then set ADMIN_TOKEN to a strong secret
docker compose up -d --build
```

Open <https://localhost:8000/admin> and sign in with the value of `ADMIN_TOKEN`.
The browser warns once about the self-signed certificate (see [HTTPS](#https) below).

Without compose:

```bash
docker build -t hue-lights .
docker run -d --name hue-lights -p 8000:8000 -e ADMIN_TOKEN=change-me -v "$PWD/data:/data" hue-lights
```

To listen on another port, set `PORT` (for example `-e PORT=8443 -p 8443:8443`).

### Environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ADMIN_TOKEN` | **yes** | – | Admin page login token. The container refuses to start without it. |
| `SESSION_SECRET` | no | random per start | Signs the admin session cookie. Set it to keep admins logged in across restarts. |
| `SESSION_TTL_HOURS` | no | `12` | Admin session lifetime. |
| `DISCOVERY_TIMEOUT_SECONDS` | no | `5` | How long mDNS discovery listens. |
| `DATA_DIR` | no | `/data` | Where `config.json` (bridge pairing, lights, tokens) and generated certificates are stored. Mount it as a volume. |
| `PORT` | no | `8000` | Port the service listens on inside the container. |
| `HOST` | no | `0.0.0.0` | Listen address. |
| `TLS_ENABLED` | no | `true` | Serve HTTPS. `false` = plain HTTP (e.g. behind your own reverse proxy). |
| `TLS_HOSTNAMES` | no | `localhost,127.0.0.1,<hostname>` | DNS names / IPs written into the generated certificate. |
| `TLS_CERT_DAYS` | no | `3650` | Validity of the generated certificate. |
| `TLS_REGENERATE` | no | `false` | Replace the generated certificate on this start (use once). |
| `TLS_CERT_FILE` / `TLS_KEY_FILE` | no | `$DATA_DIR/certs/server.{crt,key}` | Use your own PEM certificate and key instead (set both; never overwritten). |

### HTTPS

HTTPS is on by default. On first start the service generates a self-signed
certificate and RSA-2048 key into `data/certs/` and reuses them on every later
start, so the fingerprint stays stable. The admin page shows the certificate's
fingerprint, hostnames and expiry and offers a **Download certificate** button.

Clients have to trust that certificate explicitly:

```bash
# curl
curl --cacert hue-lights.crt https://localhost:8000/api/v1/health

# Python requests
requests.get("https://localhost:8000/api/v1/health", verify="hue-lights.crt")

# macOS: trust it system-wide (then restart the browser)
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain hue-lights.crt

# Debian/Ubuntu
sudo cp hue-lights.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates
```

Other applications must connect with a hostname or IP that is listed in the
certificate. Add the address they use to `TLS_HOSTNAMES`, then restart once with
`TLS_REGENERATE=true`. To use your own certificate, mount it and set
`TLS_CERT_FILE` and `TLS_KEY_FILE`. To run plain HTTP behind a reverse proxy
that terminates TLS, set `TLS_ENABLED=false`.

### Bridge discovery inside Docker

mDNS discovery only works when the container shares the host network
(`network_mode: host`, Linux only). On Docker Desktop for macOS/Windows the
admin page falls back to Hue's cloud discovery (works when the host and the
bridge share the same public IP) or you can type the bridge IP manually.

## Admin page (`/admin`)

1. **Hue Bridge** – search the network (or enter an IP), pick the bridge, then
   press the physical link button within 60 seconds. The Hue username is stored
   in `data/config.json` for future use. "Unpair / reset" clears it.
2. **Alert lights** – tick the lights that should show alerts.
3. **Glow levels** – min/max brightness (1–254) and glow period for pulsing.
4. **Test alert** – run any level/mode for N seconds, or clear the alert.
5. **API tokens** – generate tokens for other applications (shown once, stored hashed) and revoke them.
6. **API documentation** – reference and `curl` examples, plus links to Swagger UI (`/docs`).

## Alert API

All alert endpoints require `Authorization: Bearer <token>` (or `X-API-Key: <token>`).

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/alert` | Start an alert (`{"level": "amber"|"red", "mode": "glow"|"dead", "duration_seconds": 60}`); replaces a running alert. |
| `GET` | `/api/v1/alert` | Current alert state. |
| `DELETE` | `/api/v1/alert` | Clear the alert and restore the lights to their pre-alert state. |
| `GET` | `/api/v1/health` | Liveness and pairing status (no auth). |

```bash
curl --cacert hue-lights.crt -X POST https://localhost:8000/api/v1/alert \
  -H "Authorization: Bearer $HUE_TOKEN" -H "Content-Type: application/json" \
  -d '{"level": "red", "mode": "glow"}'

curl --cacert hue-lights.crt -X DELETE https://localhost:8000/api/v1/alert -H "Authorization: Bearer $HUE_TOKEN"
```

Errors: `401` bad token, `409` no bridge paired / no lights selected, `422` invalid body.

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
ADMIN_TOKEN=dev DATA_DIR=./data python -m app.serve          # https://127.0.0.1:8000
ADMIN_TOKEN=dev DATA_DIR=./data TLS_ENABLED=false uvicorn app.main:app_factory --factory --reload
pytest
```

To work on the admin UI without real hardware, run the service against an
in-memory fake bridge (discovery finds one bridge, pairing succeeds after two
"waiting" polls, three fake lights):

```bash
python scripts/dev_fake_server.py     # admin token "dev", http://127.0.0.1:8765/admin
```

## Layout

```
app/
  main.py          app factory, routers, lifespan
  serve.py         entrypoint: settings -> certificate -> uvicorn (HTTPS)
  tls.py           self-signed certificate generation / inspection
  config.py        environment settings (ADMIN_TOKEN, ...)
  storage.py       JSON store: bridge, selected lights, levels, hashed tokens
  hue_client.py    huesdk wrapper: discovery, pairing, light state
  alerts.py        alert engine (glow loop, static mode, restore on clear)
  auth.py          admin session cookie + API bearer tokens
  schemas.py       pydantic models
  routers/         admin.py (pages + admin JSON), api.py (/api/v1)
  templates/       Jinja2 pages
  static/          admin.js, style.css
tests/             pytest suite with a fake Hue client
scripts/           dev_fake_server.py (UI development without a bridge)
data/              runtime config.json (volume)
```
