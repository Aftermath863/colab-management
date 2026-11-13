"""Unit tests for Master Key lifecycle, AES-256-GCM Vault, PBKDF2 hashing, and JWT tokens."""

import base64
import os
import stat
import tempfile
import time
import unittest

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.security import (
    AuthenticationError,
    DecryptionError,
    SecurityError,
    create_access_token,
    decode_access_token,
    decrypt_secret,
    encrypt_secret,
    get_master_key,
    hash_password,
    reset_master_key_cache,
    verify_admin_password,
    verify_password,
)


class TestSecurityVault(unittest.TestCase):
    def setUp(self):
        reset_master_key_cache()
        self.orig_env_key = os.environ.get("MASTER_KEY")
        self.orig_key_path = settings.MASTER_KEY_PATH
        if "MASTER_KEY" in os.environ:
            del os.environ["MASTER_KEY"]

    def tearDown(self):
        reset_master_key_cache()
        if self.orig_env_key is not None:
            os.environ["MASTER_KEY"] = self.orig_env_key
        elif "MASTER_KEY" in os.environ:
            del os.environ["MASTER_KEY"]
        settings.MASTER_KEY_PATH = self.orig_key_path

    def test_master_key_from_env_hex(self):
        hex_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        os.environ["MASTER_KEY"] = hex_key
        key = get_master_key()
        self.assertEqual(len(key), 32)
        self.assertEqual(key, bytes.fromhex(hex_key))

    def test_master_key_from_env_raw_bytes(self):
        raw_key = "12345678901234567890123456789012"
        os.environ["MASTER_KEY"] = raw_key
        key = get_master_key()
        self.assertEqual(len(key), 32)
        self.assertEqual(key, raw_key.encode("utf-8"))

    def test_master_key_from_env_base64(self):
        raw = os.urandom(32)
        b64 = base64.b64encode(raw).decode("ascii")
        os.environ["MASTER_KEY"] = b64
        key = get_master_key()
        self.assertEqual(len(key), 32)
        self.assertEqual(key, raw)

    def test_master_key_auto_generation_and_file_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            key_file = os.path.join(tmpdir, ".master_key")
            settings.MASTER_KEY_PATH = key_file

            key1 = get_master_key()
            self.assertEqual(len(key1), 32)
            self.assertTrue(os.path.exists(key_file))

            # Verify POSIX file permissions are 0600 (owner read/write only)
            st = os.stat(key_file)
            mode = stat.S_IMODE(st.st_mode)
            self.assertEqual(mode, 0o600)

            # Reset cache and ensure reading existing file returns identical key
            reset_master_key_cache()
            key2 = get_master_key()
            self.assertEqual(key1, key2)

    def test_aes_256_gcm_encrypt_decrypt_roundtrip(self):
        plaintext = "secret_colab_oauth_refresh_token_12345!@#$%"
        ciphertext = encrypt_secret(plaintext)

        self.assertIsInstance(ciphertext, str)
        self.assertNotEqual(ciphertext, plaintext)

        decrypted = decrypt_secret(ciphertext)
        self.assertEqual(decrypted, plaintext)

    def test_aes_256_gcm_nonce_uniqueness(self):
        plaintext = "identical_plaintext"
        c1 = encrypt_secret(plaintext)
        c2 = encrypt_secret(plaintext)
        # Even with identical plaintext, nonces must be distinct -> ciphertexts distinct
        self.assertNotEqual(c1, c2)
        self.assertEqual(decrypt_secret(c1), plaintext)
        self.assertEqual(decrypt_secret(c2), plaintext)

    def test_aes_256_gcm_tamper_detection(self):
        ciphertext = encrypt_secret("sensitive_data")
        raw = bytearray(base64.b64decode(ciphertext))

        # Tamper with the last byte (authentication tag)
        raw[-1] ^= 0xFF
        tampered_ciphertext = base64.b64encode(raw).decode("utf-8")

        with self.assertRaises(DecryptionError):
            decrypt_secret(tampered_ciphertext)

    def test_aes_256_gcm_wrong_key(self):
        key1 = b"A" * 32
        key2 = b"B" * 32
        ciphertext = encrypt_secret("data", key=key1)

        with self.assertRaises(DecryptionError):
            decrypt_secret(ciphertext, key=key2)

    def test_pbkdf2_password_hashing_and_verification(self):
        password = "AdminSuperSecretPassword2026!"
        hashed = hash_password(password)

        self.assertTrue(hashed.startswith("pbkdf2:sha256:100000$"))
        self.assertTrue(verify_password(password, hashed))
        self.assertFalse(verify_password("WrongPassword!", hashed))

    def test_verify_admin_password_plaintext_and_hashed(self):
        settings.ADMIN_PASSWORD = "my_custom_password"
        settings.ADMIN_PASSWORD_HASH = ""
        self.assertTrue(verify_admin_password("my_custom_password"))
        self.assertFalse(verify_admin_password("wrong"))

        # With hash
        settings.ADMIN_PASSWORD_HASH = hash_password("hashed_admin_pass")
        self.assertTrue(verify_admin_password("hashed_admin_pass"))
        self.assertFalse(verify_admin_password("other_pass"))

    def test_jwt_create_and_decode(self):
        payload = {"sub": "admin", "role": "admin"}
        token = create_access_token(payload, expires_delta_seconds=3600)
        decoded = decode_access_token(token)

        self.assertEqual(decoded["sub"], "admin")
        self.assertEqual(decoded["role"], "admin")
        self.assertIn("exp", decoded)

    def test_jwt_expired_raises_authentication_error(self):
        payload = {"sub": "admin", "role": "admin"}
        # Expired token (-10 seconds)
        token = create_access_token(payload, expires_delta_seconds=-10)

        with self.assertRaises(AuthenticationError):
            decode_access_token(token)

    def test_jwt_invalid_signature_raises_error(self):
        token = create_access_token({"sub": "admin", "role": "admin"})
        parts = token.split(".")
        # Corrupt signature part
        parts[2] = parts[2][:-3] + "xyz"
        tampered = ".".join(parts)

        with self.assertRaises(AuthenticationError):
            decode_access_token(tampered)

    def test_secret_vault_store_and_retrieve_db(self):
        """Verify end-to-end secret storage, retrieval with decryption, and deletion in SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_db = os.path.join(tmpdir, "vault_test.db")
            init_db(test_db)

            secret_key = "HUGGINGFACE_API_KEY"
            raw_secret_value = "hf_secret_token_alpha_numeric_987654321"
            description = "Token for downloading gated model weights"

            # 1. Encrypt and store in database
            ciphertext = encrypt_secret(raw_secret_value)
            secret_id = "sec_test_001"

            with get_db(test_db) as conn:
                conn.execute(
                    """
                    INSERT INTO secrets (id, key_name, value_enc, description)
                    VALUES (?, ?, ?, ?)
                    """,
                    (secret_id, secret_key, ciphertext, description),
                )

            # 2. Retrieve row and decrypt
            with get_db(test_db) as conn:
                row = conn.execute(
                    "SELECT id, key_name, value_enc, description FROM secrets WHERE id = ?",
                    (secret_id,),
                ).fetchone()

                self.assertIsNotNone(row)
                self.assertEqual(row["key_name"], secret_key)
                self.assertEqual(row["description"], description)
                decrypted_val = decrypt_secret(row["value_enc"])
                self.assertEqual(decrypted_val, raw_secret_value)

            # 3. Delete secret and verify absence
            with get_db(test_db) as conn:
                conn.execute("DELETE FROM secrets WHERE id = ?", (secret_id,))

            with get_db(test_db) as conn:
                deleted_row = conn.execute("SELECT id FROM secrets WHERE id = ?", (secret_id,)).fetchone()
                self.assertIsNone(deleted_row)


if __name__ == "__main__":
    unittest.main()
