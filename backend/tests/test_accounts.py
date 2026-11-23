"""Email + password accounts (api/accounts.py)."""
import httpx


def _signup(c, email, password="correct horse 1", **extra):
    return c.post("/v1/auth/signup", json={"email": email, "password": password, **extra})


def test_signup_login_logout_and_trial_credits(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        r = _signup(c, "New.User@Example.com", name="New User")
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["token"].startswith("sess_") and body["user"]["roles"] == ["buyer"]
        assert body["user"]["email"] == "new.user@example.com"
        h = {"authorization": "Bearer " + body["token"]}
        w = c.get("/v1/wallet", headers=h).json()
        assert w["balance_credits"] == 500 and w["granted_credits"] == 500
        assert c.get("/v1/me", headers=h).json()["email"] == "new.user@example.com"

        assert _signup(c, "new.user@example.com").status_code == 409
        assert _signup(c, "sam@demo.dev").status_code == 409  # demo accounts can't be claimed
        assert _signup(c, "not-an-email").status_code == 422
        assert _signup(c, "short@example.com", password="short").status_code == 422

        bad = c.post("/v1/auth/login", json={"email": "new.user@example.com", "password": "wrong password"})
        assert bad.status_code == 401 and bad.json()["error"] == "invalid_credentials"
        unknown = c.post("/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever1"})
        assert unknown.status_code == 401 and unknown.json() == bad.json()
        ok = c.post("/v1/auth/login", json={"email": "NEW.user@example.com", "password": "correct horse 1"})
        assert ok.status_code == 200
        h2 = {"authorization": "Bearer " + ok.json()["token"]}
        assert c.post("/v1/auth/logout", headers=h2).json() == {"ok": True}
        assert c.get("/v1/me", headers=h2).status_code == 401
        assert c.get("/v1/me", headers=h).status_code == 200  # other sessions stay valid
        assert c.get("/v1/me", headers={"authorization": "Bearer sess_forged"}).status_code == 401


def test_creator_role_switch_and_demo_tokens(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        h = {"authorization": "Bearer " + _signup(c, "maker@example.com").json()["token"]}
        assert c.get("/v1/creator/analytics", headers=h).status_code == 403
        assert c.post("/v1/me/creator", headers=h).json()["roles"] == ["buyer", "creator"]
        assert c.get("/v1/creator/analytics", headers=h).status_code == 200
        assert c.post("/v1/me/creator", headers=h).json()["roles"] == ["buyer", "creator"]  # idempotent
        assert c.get("/v1/me", headers={"authorization": "Bearer tok_admin"}).json()["roles"][0] == "admin"


def test_admin_emails_never_granted_by_password_signup(live_server, monkeypatch):
    from app import auth
    monkeypatch.setattr(auth, "ADMIN_EMAILS", {"boss@example.com"})
    with httpx.Client(base_url=live_server, timeout=30) as c:
        assert "admin" not in _signup(c, "boss@example.com").json()["user"]["roles"]


def test_login_throttle(live_server):
    with httpx.Client(base_url=live_server, timeout=30) as c:
        _signup(c, "throttle@example.com")
        codes = [c.post("/v1/auth/login", json={"email": "throttle@example.com", "password": "nope nope"}).status_code
                 for _ in range(6)]
        assert codes == [401] * 5 + [429]


def test_password_hashes_are_salted():
    from app.services.passwords import hash_password, verify_password
    a, b = hash_password("same password"), hash_password("same password")
    assert a != b and verify_password("same password", a) and not verify_password("other", a)
    assert not verify_password("x", "garbage")
