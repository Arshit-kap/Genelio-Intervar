"""Unit tests for the accounts auth flow.

Coverage:
  Signup  — happy path, duplicate email, password mismatch, terms not accepted,
             weak password
  Login   — happy path, wrong password, unknown email
  /me     — authenticated GET, unauthenticated GET, PATCH profile
  set_password — happy path, wrong current password
  Token   — refresh, verify
"""
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from .models import User

SIGNUP_URL   = "/auth/users/"
LOGIN_URL    = "/auth/jwt/create/"
ME_URL       = "/auth/users/me/"
SET_PWD_URL  = "/auth/users/set_password/"
REFRESH_URL  = "/auth/jwt/refresh/"
VERIFY_URL   = "/auth/jwt/verify/"

VALID_PAYLOAD = {
    "email":          "alice@example.com",
    "first_name":     "Alice",
    "last_name":      "Smith",
    "password":       "Sup3rSecret!",
    "re_password":    "Sup3rSecret!",
    "agreed_to_terms": True,
}


def _signup(client, payload=None):
    return client.post(SIGNUP_URL, payload or VALID_PAYLOAD, format="json")


def _login(client, email=VALID_PAYLOAD["email"], password=VALID_PAYLOAD["password"]):
    return client.post(LOGIN_URL, {"email": email, "password": password}, format="json")


class SignupTests(APITestCase):
    def setUp(self):
        self.client = APIClient()

    # ------------------------------------------------------------------ happy
    def test_signup_returns_201_and_user_fields(self):
        resp = _signup(self.client)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        data = resp.data
        self.assertEqual(data["email"], VALID_PAYLOAD["email"])
        self.assertIn("id", data)
        # write-only fields must NOT leak back
        self.assertNotIn("password",    data)
        self.assertNotIn("re_password", data)
        self.assertNotIn("agreed_to_terms", data)

    def test_user_is_active_after_signup(self):
        _signup(self.client)
        user = User.objects.get(email=VALID_PAYLOAD["email"])
        self.assertTrue(user.is_active)

    def test_password_is_hashed(self):
        _signup(self.client)
        user = User.objects.get(email=VALID_PAYLOAD["email"])
        self.assertTrue(user.check_password(VALID_PAYLOAD["password"]))
        self.assertNotEqual(user.password, VALID_PAYLOAD["password"])

    def test_agreed_to_terms_stored(self):
        _signup(self.client)
        user = User.objects.get(email=VALID_PAYLOAD["email"])
        self.assertTrue(user.agreed_to_terms)

    # --------------------------------------------------------------- failures
    def test_duplicate_email_returns_400(self):
        _signup(self.client)
        resp = _signup(self.client)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_password_mismatch_returns_400(self):
        payload = {**VALID_PAYLOAD, "email": "bob@example.com", "re_password": "WRONG"}
        resp = _signup(self.client, payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("re_password", resp.data)

    def test_terms_not_accepted_returns_400(self):
        payload = {**VALID_PAYLOAD, "email": "carol@example.com", "agreed_to_terms": False}
        resp = _signup(self.client, payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("agreed_to_terms", resp.data)

    def test_weak_password_returns_400(self):
        payload = {**VALID_PAYLOAD, "email": "dan@example.com",
                   "password": "password", "re_password": "password"}
        resp = _signup(self.client, payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_email_returns_400(self):
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "email"}
        resp = _signup(self.client, payload)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class LoginTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        _signup(self.client)

    # ------------------------------------------------------------------ happy
    def test_login_returns_access_and_refresh_tokens(self):
        resp = _login(self.client)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertIn("access",  resp.data)
        self.assertIn("refresh", resp.data)

    # --------------------------------------------------------------- failures
    def test_wrong_password_returns_401(self):
        resp = _login(self.client, password="WrongPass99!")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unknown_email_returns_401(self):
        resp = _login(self.client, email="nobody@example.com")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_empty_password_returns_400(self):
        resp = self.client.post(LOGIN_URL, {"email": VALID_PAYLOAD["email"]}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class MeEndpointTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        _signup(self.client)
        resp = _login(self.client)
        self.access = resp.data["access"]

    def _auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")

    # ------------------------------------------------------------------ happy
    def test_get_me_returns_user_data(self):
        self._auth()
        resp = self.client.get(ME_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["email"], VALID_PAYLOAD["email"])
        self.assertEqual(resp.data["first_name"], VALID_PAYLOAD["first_name"])

    def test_patch_me_updates_username(self):
        self._auth()
        resp = self.client.patch(ME_URL, {"username": "alice_42"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data["username"], "alice_42")

    def test_patch_me_updates_name(self):
        self._auth()
        resp = self.client.patch(ME_URL, {"first_name": "Alicia"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["first_name"], "Alicia")

    # --------------------------------------------------------------- failures
    def test_get_me_unauthenticated_returns_401(self):
        resp = self.client.get(ME_URL)
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_patch_me_cannot_change_email(self):
        self._auth()
        resp = self.client.patch(ME_URL, {"email": "hacked@example.com"}, format="json")
        # email is read_only — value must not change
        self.assertNotEqual(
            User.objects.get(email=VALID_PAYLOAD["email"]).email,
            "hacked@example.com",
        )


class SetPasswordTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        _signup(self.client)
        resp = _login(self.client)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")

    def test_set_password_success(self):
        resp = self.client.post(SET_PWD_URL, {
            "current_password": VALID_PAYLOAD["password"],
            "new_password":     "NewP@ssw0rd!",
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        # Can log in with the new password
        self.client.credentials()
        resp = _login(self.client, password="NewP@ssw0rd!")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_wrong_current_password_returns_400(self):
        resp = self.client.post(SET_PWD_URL, {
            "current_password": "NotMyPassword!",
            "new_password":     "NewP@ssw0rd!",
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_weak_new_password_returns_400(self):
        resp = self.client.post(SET_PWD_URL, {
            "current_password": VALID_PAYLOAD["password"],
            "new_password":     "password",
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_set_password_unauthenticated_returns_401(self):
        self.client.credentials()
        resp = self.client.post(SET_PWD_URL, {
            "current_password": VALID_PAYLOAD["password"],
            "new_password":     "NewP@ssw0rd!",
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class TokenRefreshVerifyTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        _signup(self.client)
        resp = _login(self.client)
        self.access  = resp.data["access"]
        self.refresh = resp.data["refresh"]

    def test_refresh_returns_new_access_token(self):
        resp = self.client.post(REFRESH_URL, {"refresh": self.refresh}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertIn("access", resp.data)

    def test_verify_valid_token(self):
        resp = self.client.post(VERIFY_URL, {"token": self.access}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_verify_invalid_token_returns_401(self):
        resp = self.client.post(VERIFY_URL, {"token": "not.a.token"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_refresh_returns_401(self):
        resp = self.client.post(REFRESH_URL, {"refresh": "bad_token"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class FullRoundTripTest(APITestCase):
    """One sequential integration test that mirrors the Postman collection runner."""

    def test_signup_login_me_setpassword_refresh(self):
        client = APIClient()

        # 1. Signup
        resp = client.post(SIGNUP_URL, VALID_PAYLOAD, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        user_id = resp.data["id"]

        # 2. Login
        resp = client.post(LOGIN_URL, {
            "email": VALID_PAYLOAD["email"],
            "password": VALID_PAYLOAD["password"],
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        access, refresh = resp.data["access"], resp.data["refresh"]

        # 3. /me
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        resp = client.get(ME_URL)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["id"], user_id)

        # 4. Update profile
        resp = client.patch(ME_URL, {"username": "alice_42", "first_name": "Alicia"}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["username"], "alice_42")

        # 5. Change password
        resp = client.post(SET_PWD_URL, {
            "current_password": VALID_PAYLOAD["password"],
            "new_password":     "NewP@ssw0rd!",
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

        # 6. Re-login with new password
        client.credentials()
        resp = client.post(LOGIN_URL, {
            "email":    VALID_PAYLOAD["email"],
            "password": "NewP@ssw0rd!",
        }, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        new_access = resp.data["access"]

        # 7. Refresh token
        resp = client.post(REFRESH_URL, {"refresh": refresh}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # 8. Verify new access token
        resp = client.post(VERIFY_URL, {"token": new_access}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
