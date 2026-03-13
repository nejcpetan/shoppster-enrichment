"""Tests for Phase 3: Authentication System."""

import pytest
from tests.conftest import auth_header


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

class TestPasswordHashing:
    def test_hash_and_verify(self):
        from auth.security import hash_password, verify_password
        hashed = hash_password("mypassword")
        assert hashed != "mypassword"
        assert verify_password("mypassword", hashed)
        assert not verify_password("wrong", hashed)

    def test_different_hashes_for_same_password(self):
        from auth.security import hash_password
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt uses random salt


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------

class TestJWT:
    def test_create_and_decode(self):
        from auth.security import create_access_token, decode_access_token
        token = create_access_token({"sub": "42", "role": "admin"})
        payload = decode_access_token(token)
        assert payload["sub"] == "42"
        assert payload["role"] == "admin"
        assert "exp" in payload

    def test_decode_invalid_token(self):
        from auth.security import decode_access_token
        assert decode_access_token("garbage.token.here") is None

    def test_decode_empty_string(self):
        from auth.security import decode_access_token
        assert decode_access_token("") is None

    def test_expired_token(self):
        from auth.security import create_access_token, decode_access_token
        from datetime import timedelta
        token = create_access_token({"sub": "1"}, expires_delta=timedelta(seconds=-1))
        assert decode_access_token(token) is None


# ---------------------------------------------------------------------------
# Login endpoint
# ---------------------------------------------------------------------------

class TestLogin:
    def test_login_success(self, client, admin_user):
        email, password = admin_user
        resp = client.post("/api/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == email
        assert data["user"]["role"] == "admin"

    def test_login_wrong_password(self, client, admin_user):
        email, _ = admin_user
        resp = client.post("/api/auth/login", json={"email": email, "password": "wrongwrong"})
        assert resp.status_code == 401

    def test_login_nonexistent_email(self, client):
        resp = client.post("/api/auth/login", json={"email": "nobody@test.com", "password": "whatever1"})
        assert resp.status_code == 401

    def test_login_normalizes_email(self, client, admin_user):
        email, password = admin_user
        resp = client.post("/api/auth/login", json={"email": f"  {email.upper()}  ", "password": password})
        # The router lowercases + strips, so this should match
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Protected endpoints
# ---------------------------------------------------------------------------

class TestEndpointProtection:
    def test_products_requires_auth(self, client):
        resp = client.get("/api/products")
        assert resp.status_code in (401, 403)

    def test_products_with_valid_token(self, client, admin_token):
        resp = client.get("/api/products", headers=auth_header(admin_token))
        assert resp.status_code == 200

    def test_products_with_invalid_token(self, client):
        resp = client.get("/api/products", headers=auth_header("bad.token.here"))
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /me endpoint
# ---------------------------------------------------------------------------

class TestMe:
    def test_me_returns_user_info(self, client, admin_token):
        resp = client.get("/api/auth/me", headers=auth_header(admin_token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "admin@test.com"
        assert data["role"] == "admin"

    def test_me_without_auth(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Register (admin-only)
# ---------------------------------------------------------------------------

class TestRegister:
    def test_admin_can_register_viewer(self, client, admin_token):
        resp = client.post(
            "/api/auth/register",
            json={"email": "new@test.com", "password": "newpass1234", "full_name": "New User", "role": "viewer"},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 201

    def test_viewer_cannot_register(self, client, viewer_token):
        resp = client.post(
            "/api/auth/register",
            json={"email": "new2@test.com", "password": "newpass1234", "role": "viewer"},
            headers=auth_header(viewer_token),
        )
        assert resp.status_code == 403

    def test_register_duplicate_email(self, client, admin_token, admin_user):
        email, _ = admin_user
        resp = client.post(
            "/api/auth/register",
            json={"email": email, "password": "anotherpass1", "role": "viewer"},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 409

    def test_register_short_password(self, client, admin_token):
        resp = client.post(
            "/api/auth/register",
            json={"email": "short@test.com", "password": "short", "role": "viewer"},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 400

    def test_register_invalid_role(self, client, admin_token):
        resp = client.post(
            "/api/auth/register",
            json={"email": "bad@test.com", "password": "password1234", "role": "superadmin"},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Role-based access
# ---------------------------------------------------------------------------

class TestRoleAccess:
    def test_viewer_can_read_products(self, client, viewer_token):
        resp = client.get("/api/products", headers=auth_header(viewer_token))
        assert resp.status_code == 200

    def test_viewer_cannot_upload(self, client, viewer_token):
        # Attempt a POST to an admin-only endpoint
        resp = client.post(
            "/api/products/process-all",
            headers=auth_header(viewer_token),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------

class TestChangePassword:
    def test_change_password_success(self, client, admin_user, admin_token):
        _, old_password = admin_user
        resp = client.post(
            "/api/auth/change-password",
            json={"current_password": old_password, "new_password": "brandnewpass1"},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 200

        # Login with new password works
        resp2 = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "brandnewpass1"})
        assert resp2.status_code == 200

    def test_change_password_wrong_current(self, client, admin_token):
        resp = client.post(
            "/api/auth/change-password",
            json={"current_password": "wrongcurrent", "new_password": "newpass12345"},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 400

    def test_change_password_too_short(self, client, admin_user, admin_token):
        _, old_password = admin_user
        resp = client.post(
            "/api/auth/change-password",
            json={"current_password": old_password, "new_password": "short"},
            headers=auth_header(admin_token),
        )
        assert resp.status_code == 400
