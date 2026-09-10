from __future__ import annotations

import hashlib
import hmac

from app.core.security import verify_netbox_signature, verify_twenty_token


class TestVerifyNetboxSignature:
    def test_valid_signature(self):
        payload = b'{"model":"tenant","action":"created"}'
        secret = "my-secret-key"
        sig = hmac.new(secret.encode(), payload, hashlib.sha512).hexdigest()
        assert verify_netbox_signature(payload, sig, secret) is True

    def test_invalid_signature(self):
        payload = b'{"model":"tenant"}'
        assert verify_netbox_signature(payload, "bad-sig", "secret") is False

    def test_empty_payload(self):
        secret = "key"
        sig = hmac.new(secret.encode(), b"", hashlib.sha512).hexdigest()
        assert verify_netbox_signature(b"", sig, secret) is True

    def test_different_secrets_fail(self):
        payload = b"data"
        sig = hmac.new(b"secret-a", payload, hashlib.sha512).hexdigest()
        assert verify_netbox_signature(payload, sig, "secret-b") is False

    def test_hex_encoding(self):
        payload = b"test"
        secret = "abc"
        sig = hmac.new(secret.encode(), payload, hashlib.sha512).hexdigest()
        assert len(sig) == 128  # SHA-512 hex digest length
        assert all(c in "0123456789abcdef" for c in sig)


class TestVerifyTwentyToken:
    def test_valid_token(self):
        assert verify_twenty_token("abc-123", "abc-123") is True

    def test_invalid_token(self):
        assert verify_twenty_token("wrong", "correct") is False

    def test_empty_token_matches_empty_secret(self):
        assert verify_twenty_token("", "") is True

    def test_empty_token_does_not_match_secret(self):
        assert verify_twenty_token("", "something") is False

    def test_case_sensitive(self):
        assert verify_twenty_token("ABC", "abc") is False


class TestVerifyTwentySignature:
    def test_valid_signature(self):
        from app.core.security import verify_twenty_signature

        payload = b'{"event":"company.created"}'
        secret = "twenty-secret"
        import time

        ts = str(int(time.time() * 1000))
        digest = hmac.new(secret.encode(), f"{ts}:".encode() + payload, hashlib.sha256).hexdigest()
        assert verify_twenty_signature(payload, digest, ts, secret) is True

    def test_invalid_signature(self):
        from app.core.security import verify_twenty_signature

        payload = b"data"
        assert verify_twenty_signature(payload, "badsig", "123", "secret") is False

    def test_expired_timestamp(self):
        import time

        from app.core.security import verify_twenty_signature

        payload = b"data"
        secret = "secret"
        ts = str(int(time.time() * 1000) - 10 * 60 * 1000)  # 10 minutes ago
        digest = hmac.new(secret.encode(), f"{ts}:".encode() + payload, hashlib.sha256).hexdigest()
        assert verify_twenty_signature(payload, digest, ts, secret, max_age_seconds=300) is False

    def test_missing_signature_or_timestamp(self):
        from app.core.security import verify_twenty_signature

        assert verify_twenty_signature(b"x", None, "123", "s") is False
        assert verify_twenty_signature(b"x", "sig", None, "s") is False
