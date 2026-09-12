# Hue Alert Lights — Implementation Plan

Status: **implemented** (2026-09-12) — see README.md for usage

## Goal
A small self-hosted service that (1) pairs with a Philips Hue Bridge through an
admin page and (2) exposes a token-protected HTTP API so other applications can
turn selected Hue lights into Amber/Red alert indicators (glowing or static).

## Stack
| Concern          | Choice                                   | Why |
|------------------|------------------------------------------|-----|
| Hue access       | `huesdk` 1.8 (required)                  | discovery (mDNS + cloud), pairing, light state |
| Web/API          | FastAPI + Uvicorn                        | tiny, async, free OpenAPI docs at `/docs` |
| Admin UI         | Jinja2 templates + vanilla JS + one CSS  | no build step, single container |
| Persistence      | one JSON file at `/data/config.json`     | "simple project"; Docker volume |
| Alert engine     | asyncio background task in-process       | glow loop needs a long-running worker |
| Packaging        | Dockerfile + docker-compose.yml          | requested |
| Tests            | pytest with a fake Hue client            | engine + auth logic without a real bridge |

## Folder structure
```
hue-lights/
├── PLAN.md                 # this file
├── README.md               # setup, run, API quick reference
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example            # ADMIN_TOKEN=..., DATA_DIR=/data
├── .gitignore / .dockerignore
├── app/
│   ├── main.py             # FastAPI app, lifespan (start/stop alert engine), routers
│   ├── config.py           # env settings: ADMIN_TOKEN, DATA_DIR, PORT
│   ├── storage.py          # JSON store: bridge, hue username, selected lights, levels, tokens
│   ├── hue_client.py       # thin wrapper over huesdk: discover, pair, list lights, set_state
│   ├── alerts.py           # AlertEngine: current alert, glow loop, restore on clear
│   ├── auth.py             # admin cookie session + API bearer-token dependency
│   ├── schemas.py          # pydantic request/response models
│   ├── routers/
│   │   ├── admin.py        # /admin pages + /admin/* JSON endpoints (cookie auth)
│   │   └── api.py          # /api/v1/alert endpoints (bearer token auth)
│   ├── templates/          # base.html, login.html, admin.html, api_docs.html
│   └── static/             # admin.js, style.css
├── data/                   # runtime config.json (gitignored, mounted volume)
└── tests/                  # test_alerts.py, test_auth.py, test_api.py
```

## Output 1 — Admin page (`/admin`)
Auth: a predefined token from env `ADMIN_TOKEN`. Login form → constant-time
compare → signed HttpOnly cookie. Logout button.

Sections, top to bottom:
1. **Bridge setup** (shown first when no bridge is stored)
   - "Search for Hue Bridge" button → `POST /admin/bridge/discover` runs
     `Discover.find_hue_bridge_mdns()` and `Discover.find_hue_bridge()` (cloud),
     merges results, returns `[{id, ip, name}]`. Also a manual IP field as fallback.
   - User selects a bridge → page shows "Press the link button on your bridge",
     then polls `POST /admin/bridge/pair {ip}` every 2 s for up to 60 s. Each call
     runs `Hue.connect(ip)`; the "link button not pressed" error is mapped to
     `{status: "waiting"}`; on success the username is stored and the page reloads.
   - Once paired: shows bridge ip/id, an "Unpair / reset" button.
2. **Lights** — `GET /admin/lights` lists all lights (id, name, on/off, reachable)
   with checkboxes; "Save selection" persists the chosen light ids.
3. **Alert settings** — min and max brightness sliders (1–254) for glowing,
   glow period in seconds (default 1.5 s); "Save".
4. **Test alert** — level select (Amber/Red), mode select (Glow/Dead),
   duration (default 10 s), "Run test" and "Clear" buttons. Uses the same engine
   as the public API so the test is representative.
5. **API tokens** — name field + "Generate" → token shown **once**; table of
   existing tokens (name, prefix, created, last used) with "Revoke".
   Tokens stored as SHA-256 hashes.
6. **API documentation** — rendered section with endpoint reference and
   copy-pasteable `curl` examples, plus links to `/docs` (Swagger UI) and
   `/openapi.json`.

## Output 2 — Alert API (`/api/v1`, header `Authorization: Bearer <token>`)
| Method | Path              | Body / result |
|--------|-------------------|---------------|
| POST   | `/api/v1/alert`   | `{"level": "amber"\|"red", "mode": "glow"\|"dead", "duration_seconds": int?}` → `{status, alert}` |
| GET    | `/api/v1/alert`   | current alert state or `{"active": false}` |
| DELETE | `/api/v1/alert`   | clears alert, restores prior light state |
| GET    | `/api/v1/health`  | unauthenticated liveness + `bridge_paired` flag |

Semantics:
- A new POST replaces any running alert (last write wins).
- `glow`: engine loop alternates brightness between min and max with
  `transitiontime` ≈ half the period, color fixed (Amber `#FFBF00`, Red `#FF0000`, via `hexa_to_xy`).
- `dead`: single PUT — on, colour, brightness = max; no further changes.
- Optional `duration_seconds` auto-clears afterwards; otherwise runs until DELETE.
- Before the first PUT the engine snapshots each selected light's state
  (on/bri/xy) and restores it on clear.
- Errors: 401 bad token, 409 no bridge paired or no lights selected, 422 bad body.

## huesdk notes that shape the design
- `Light.on()/off()` are gated by cached state, so `hue_client.set_state()` will
  call `Hue.put('/{user}/lights/{id}/state', json)` directly with a full body.
- The bridge cert is self-signed; huesdk already uses `verify=False`, we'll
  silence the urllib3 warning.
- Blocking `requests`/`zeroconf` calls are run in a threadpool from async handlers.
- mDNS discovery only works with `network_mode: host` (Linux). On Docker Desktop
  for macOS/Windows, cloud discovery and manual IP entry are the fallbacks; this
  is documented in README and shown as a hint in the UI.

## Docker
- `python:3.12-slim`, non-root user, `pip install -r requirements.txt`,
  `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- Admin authentication is configured **only** through the container environment:
  - `ADMIN_TOKEN` (required) — the predefined admin login token. The app refuses
    to start with a clear error if it is missing or empty; no default is baked in.
  - `SESSION_SECRET` (optional) — key used to sign the admin session cookie;
    auto-generated at startup if unset (sessions then reset on restart).
  - `SESSION_TTL_HOURS` (optional, default 12).
  - Declared with `ENV` placeholders in the Dockerfile, wired in
    `docker-compose.yml` as `environment: - ADMIN_TOKEN=${ADMIN_TOKEN}` read from
    `.env`, and listed in `.env.example`. Documented in README with
    `docker run -e ADMIN_TOKEN=... -v ./data:/data -p 8000:8000 hue-lights`.
- `docker-compose.yml`: volume `./data:/data`, port `8000`, commented
  `network_mode: host` option for mDNS.

## Implementation order
1. storage + config + hue_client (+ fake client for tests)
2. alert engine + tests
3. auth (admin cookie, API tokens) + tests
4. API router + tests
5. admin router + templates + JS
6. Dockerfile, compose, README
7. Manual end-to-end check against a real bridge (you) / fake bridge (me)

---

# Phase 2 — HTTPS with a self-generated certificate

Status: **implemented** (2026-09-12)

## Goal
Serve the admin page and API over HTTPS out of the box, using a self-signed
certificate the container generates itself on first start and keeps in the
data volume, so other applications and browsers can trust one stable cert.

## Approach
Uvicorn serves TLS natively (`ssl_certfile` / `ssl_keyfile`). No reverse proxy,
no extra container. Certificate generation is done in Python with the
`cryptography` library (one new dependency, ~4 MB) rather than shelling out to
the `openssl` CLI, so it is portable (macOS dev, Linux container) and unit-testable.

## Behaviour
- On start, if `TLS_ENABLED=true` (the new default) and no cert/key exist at
  the configured paths, generate a self-signed cert + RSA-2048 key and store
  them under `/data/certs/` (persisted by the existing volume). Subsequent
  starts reuse them, so fingerprints stay stable.
- Certificate contents: CN `hue-lights`, Subject Alternative Names from
  `TLS_HOSTNAMES` (DNS names and IPs, comma separated; default
  `localhost,127.0.0.1` plus the container hostname), validity `TLS_CERT_DAYS`
  (default 3650), `KeyUsage`/`ExtendedKeyUsage=serverAuth`, self-issued.
  Key file written with mode 0600.
- Regeneration: delete the two files, or start once with `TLS_REGENERATE=true`.
- Bring-your-own cert: point `TLS_CERT_FILE` / `TLS_KEY_FILE` at existing files
  (mounted read-only) and nothing is generated.
- `TLS_ENABLED=false` keeps today's plain HTTP behaviour (for use behind an
  existing reverse proxy).
- Container port stays **8000**; only the scheme changes
  (`https://localhost:8000`). Map it to 8443 on the host in compose if preferred.

## Environment variables (added)
| Variable | Default | Purpose |
|----------|---------|---------|
| `TLS_ENABLED` | `true` | Serve HTTPS; `false` = plain HTTP |
| `TLS_CERT_FILE` | `/data/certs/server.crt` | PEM certificate path |
| `TLS_KEY_FILE` | `/data/certs/server.key` | PEM private key path |
| `TLS_HOSTNAMES` | `localhost,127.0.0.1,<hostname>` | SANs baked into the generated cert |
| `TLS_CERT_DAYS` | `3650` | Validity of the generated cert |
| `TLS_REGENERATE` | `false` | Force a new cert on this start |

## Code changes
| File | Change |
|------|--------|
| `app/tls.py` (new) | `generate_self_signed(cert, key, hostnames, days)`, `ensure_certificate(settings)`, `certificate_info(path)` (subject, SANs, not_after, SHA-256 fingerprint) |
| `app/serve.py` (new) | Launcher: load settings → ensure cert → `uvicorn.run(app, ssl_certfile=…, ssl_keyfile=…)`; replaces the `uvicorn` CMD |
| `app/config.py` | Parse the `TLS_*` variables; validate that cert/key exist when provided explicitly |
| `app/routers/admin.py` | `GET /admin/tls` (cert info JSON) and `GET /admin/tls/certificate` (download PEM, admin session required) |
| `app/templates/admin.html` | New "HTTPS certificate" card: fingerprint, SANs, expiry, **Download certificate** button, short "how to trust it" text |
| `app/templates/api_docs.html` | Note on `curl --cacert server.crt` / `-k`, `requests(verify="server.crt")` |
| `app/auth.py` | No change needed: cookie already gets `secure=True` on https |
| `Dockerfile` | Add `TLS_*` ENV defaults; healthcheck uses `https://` with verification disabled (localhost self-signed); CMD `python -m app.serve` |
| `docker-compose.yml`, `.env.example` | Expose the new variables |
| `requirements.txt` | `cryptography` |
| `README.md` | HTTPS section: default behaviour, trusting the cert (macOS Keychain, Linux ca-certificates, curl, Python), disabling TLS behind a proxy |

## Tests (added)
- `tests/test_tls.py`: generated cert parses, contains the requested DNS/IP
  SANs, is self-signed, valid for the requested days, key file is 0600, key
  matches cert public key; `ensure_certificate` is idempotent (same
  fingerprint on second call) and regenerates when forced or files missing.
- `tests/test_admin.py`: cert-info endpoint and PEM download require admin
  login and return the right content type.
- `tests/test_config_storage.py`: `TLS_*` parsing, invalid `TLS_CERT_DAYS`,
  explicit cert path that does not exist → `ConfigError`.
- Manual: `docker compose up`, `curl -k https://localhost:8000/api/v1/health`,
  `curl --cacert data/certs/server.crt https://localhost:8000/...` succeeds,
  browser shows the expected self-signed warning once and the admin page loads.

## Out of scope (can be added later)
- Let's Encrypt / ACME (needs a public hostname).
- Hot-reloading a new cert without a container restart.
- Serving HTTP and HTTPS at the same time (would need two processes or a proxy).
