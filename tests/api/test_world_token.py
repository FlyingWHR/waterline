"""id_token checks against a locally generated RS256 key (no network)."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from api import world

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKS:
    def get_signing_key_from_jwt(self, _):
        return type("K", (), {"key": KEY.public_key()})()


@pytest.fixture(autouse=True)
def jwks(monkeypatch):
    monkeypatch.setitem(world._jwks, f"{world.issuer()}/.well-known/jwks.json", FakeJWKS())


def token(**over):
    now = int(time.time())
    claims = {"iss": world.issuer(), "aud": "app_test", "sub": "h1", "iat": now, "exp": now + 300,
              "auth_time": now} | over
    return jwt.encode(claims, KEY, algorithm="RS256")


def test_valid_token():
    assert world.verify_id_token(token())["sub"] == "h1"


@pytest.mark.parametrize("bad", [{"aud": "someone-else"}, {"iss": "https://evil.example"},
                                 {"exp": int(time.time()) - 60}])
def test_rejects_wrong_claims(bad):
    with pytest.raises(jwt.PyJWTError):
        world.verify_id_token(token(**bad))


def test_rejects_missing_auth_time():
    t = jwt.encode({"iss": world.issuer(), "aud": "app_test", "sub": "h1", "iat": int(time.time()),
                    "exp": int(time.time()) + 300}, KEY, algorithm="RS256")
    with pytest.raises(jwt.PyJWTError):
        world.verify_id_token(t)


def test_rejects_other_signer():
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    t = jwt.encode({"iss": world.issuer(), "aud": "app_test", "sub": "h1", "iat": int(time.time()),
                    "exp": int(time.time()) + 300, "auth_time": int(time.time())}, other, algorithm="RS256")
    with pytest.raises(jwt.PyJWTError):
        world.verify_id_token(t)
