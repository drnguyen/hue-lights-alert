from tests.conftest import ADMIN_TOKEN, bearer


def test_admin_page_redirects_when_logged_out(client):
    res = client.get("/admin")
    assert res.status_code == 303 and res.headers["location"] == "/admin/login"
    assert client.get("/").status_code in (302, 307)
    assert client.get("/admin/lights").status_code == 401


def test_login_rejects_bad_token(client):
    res = client.post("/admin/login", data={"token": "nope"})
    assert res.status_code == 401
    assert "Invalid admin token" in res.text
    assert "hue_admin_session" not in client.cookies


def test_login_and_logout(client):
    res = client.post("/admin/login", data={"token": ADMIN_TOKEN})
    assert res.status_code == 303 and res.headers["location"] == "/admin"
    assert "hue_admin_session" in client.cookies
    page = client.get("/admin")
    assert page.status_code == 200
    assert "Search for Hue Bridge" in page.text  # first-run state
    assert "API documentation" in page.text

    res = client.post("/admin/logout")
    assert res.status_code == 303
    assert client.get("/admin").status_code == 303


def test_tampered_cookie_is_rejected(client):
    client.cookies.set("hue_admin_session", "eyJhZG1pbiI6dHJ1ZX0.forged.signature")
    assert client.get("/admin/lights").status_code == 401


def test_api_requires_token(client, paired):
    assert client.get("/api/v1/alert").status_code == 401
    assert client.get("/api/v1/alert", headers=bearer("hue_bogus")).status_code == 401
    assert client.post("/api/v1/alert", json={"level": "red", "mode": "glow"}).status_code == 401
    assert client.get("/api/v1/health").status_code == 200  # no auth


def test_generated_token_works_until_revoked(admin, paired):
    created = admin.post("/admin/tokens", json={"name": "ci"}).json()
    token, record = created["token"], created["record"]
    assert admin.get("/api/v1/alert", headers=bearer(token)).status_code == 200
    assert admin.get("/api/v1/alert", headers={"X-API-Key": token}).status_code == 200

    listed = admin.get("/admin/tokens").json()["tokens"]
    assert listed[0]["name"] == "ci" and listed[0]["last_used_at"] is not None and "hash" not in listed[0]

    assert admin.delete(f"/admin/tokens/{record['id']}").status_code == 200
    assert admin.delete(f"/admin/tokens/{record['id']}").status_code == 404
    assert admin.get("/api/v1/alert", headers=bearer(token)).status_code == 401


def test_token_name_required(admin):
    assert admin.post("/admin/tokens", json={"name": ""}).status_code == 422
