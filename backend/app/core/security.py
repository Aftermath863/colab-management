"""Security, Master Key Management, AES-256-GCM Vault, and JWT Authentication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import stat
import time
from typing import Any, Dict, Optional, Union

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from fastapi import Depends, HTTPException, Security, status
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    security_bearer = HTTPBearer(auto_error=False)
except ImportError:
    security_bearer = None

from backend.app.config import settings

logger = logging.getLogger(__name__)


# --- Domain Exceptions ---

class SecurityError(Exception):
    """Base exception for all security, cryptographic, and auth failures."""
    pass


class AuthenticationError(SecurityError):
    """Raised when authentication credentials or tokens are invalid or expired."""
    pass


class DecryptionError(SecurityError):
    """Raised when decrypting vaulted secrets fails due to invalid key or tampering."""
    pass


# --- Master Key Management ---

_CACHED_MASTER_KEY: Optional[bytes] = None


def reset_master_key_cache() -> None:
    """Clear cached master key (useful in test suites when altering env or key files)."""
    global _CACHED_MASTER_KEY
    _CACHED_MASTER_KEY = None


def get_master_key() -> bytes:
    """Retrieve or generate the 32-byte AES-256 master encryption key.

    Priority:
    1. Environment variable MASTER_KEY (if non-empty; parses 64-char hex, 32 raw bytes, or base64).
    2. File at settings.MASTER_KEY_PATH (/data/.master_key).
    3. Auto-generation: os.urandom(32) persisted with 0600 file permissions.
    """
    global _CACHED_MASTER_KEY
    if _CACHED_MASTER_KEY is not None:
        return _CACHED_MASTER_KEY

    # Priority 1: Check environment variable (treat empty string as unset)
    env_key = os.getenv("MASTER_KEY", "").strip()
    if env_key:
        # Check if 64-character hex string
        if len(env_key) == 64:
            try:
                parsed_hex = bytes.fromhex(env_key)
                if len(parsed_hex) == 32:
                    _CACHED_MASTER_KEY = parsed_hex
                    return _CACHED_MASTER_KEY
            except ValueError:
                pass

        # Check if 32 raw bytes string
        raw_bytes = env_key.encode("utf-8")
        if len(raw_bytes) == 32:
            _CACHED_MASTER_KEY = raw_bytes
            return _CACHED_MASTER_KEY

        # Check if base64 encoded 32 bytes
        try:
            b64_bytes = base64.b64decode(env_key)
            if len(b64_bytes) == 32:
                _CACHED_MASTER_KEY = b64_bytes
                return _CACHED_MASTER_KEY
        except Exception:
            pass

        raise ValueError(
            "Invalid MASTER_KEY in environment: must be 32 bytes (64-character hex, 32 raw bytes, or base64)"
        )

    # Priority 2: Check persistent key file
    key_file = settings.MASTER_KEY_PATH
    if os.path.exists(key_file):
        try:
            with open(key_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if len(content) == 64:
                try:
                    _CACHED_MASTER_KEY = bytes.fromhex(content)
                    return _CACHED_MASTER_KEY
                except ValueError:
                    raise SecurityError(
                        f"Corrupted master key file at {key_file}: must contain 64-hex or 32 raw bytes"
                    )
            raw_file_bytes = content.encode("utf-8")
            if len(raw_file_bytes) == 32:
                _CACHED_MASTER_KEY = raw_file_bytes
                return _CACHED_MASTER_KEY
            if os.path.getsize(key_file) > 0:
                raise SecurityError(
                    f"Corrupted master key file at {key_file}: must contain 64-hex or 32 raw bytes"
                )
        except SecurityError:
            raise
        except Exception as e:
            raise SecurityError(f"Failed to read existing master key file {key_file}: {e}")

    # Priority 3: Generate new 32-byte key and persist with 0600 permissions
    new_key = os.urandom(32)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(key_file)), exist_ok=True)
        # Atomic creation with mode 0600
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            f.write(new_key.hex() + "\n")
        logger.info(f"Generated new 32-byte master key and saved to {key_file} (mode 0600)")
    except Exception as e:
        # If filesystem is read-only or permission denied, fall back to in-memory key
        logger.warning(f"Could not persist master key to {key_file}: {e}. Retaining in-memory.")

    _CACHED_MASTER_KEY = new_key
    return _CACHED_MASTER_KEY


# --- AES-256-GCM Vault ---

def encrypt_secret(plaintext: Union[str, bytes], key: Optional[bytes] = None) -> str:
    """Encrypt plaintext string or bytes using AES-256-GCM.

    Returns: Base64(nonce [12 bytes] + ciphertext + tag [16 bytes]).
    """
    if key is None:
        key = get_master_key()
    if len(key) != 32:
        raise ValueError(f"AES-256-GCM requires a 32-byte key, got {len(key)} bytes")

    data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    nonce = os.urandom(12)  # Standard 96-bit nonce
    aesgcm = AESGCM(key)
    ciphertext_and_tag = aesgcm.encrypt(nonce, data, None)
    payload = nonce + ciphertext_and_tag
    return base64.b64encode(payload).decode("utf-8")


def decrypt_secret(encrypted_str: str, key: Optional[bytes] = None) -> str:
    """Decrypt a vaulted Base64 string using AES-256-GCM.

    Catches tampering and tag mismatches, raising DecryptionError.
    """
    if key is None:
        key = get_master_key()
    if len(key) != 32:
        raise ValueError(f"AES-256-GCM requires a 32-byte key, got {len(key)} bytes")

    try:
        payload = base64.b64decode(encrypted_str.encode("utf-8"))
    except Exception as e:
        raise DecryptionError(f"Invalid Base64 payload: {e}")

    # Minimum length: 12-byte nonce + 16-byte tag = 28 bytes
    if len(payload) < 28:
        raise DecryptionError("Ciphertext payload is too short to be valid AES-256-GCM")

    nonce = payload[:12]
    ciphertext_and_tag = payload[12:]

    aesgcm = AESGCM(key)
    try:
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext_and_tag, None)
        return decrypted_bytes.decode("utf-8")
    except InvalidTag:
        raise DecryptionError("Authentication tag mismatch: data corrupted, tampered, or wrong master key")
    except Exception as e:
        raise DecryptionError(f"Decryption failed: {e}")


# --- Password Hashing (PBKDF2-HMAC-SHA256) ---

def hash_password(password: str, iterations: int = 100_000) -> str:
    """Hash password with PBKDF2-HMAC-SHA256 and a random 16-byte salt."""
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2:sha256:{iterations}${salt.hex()}${derived.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plaintext password against a stored PBKDF2 hash."""
    try:
        parts = hashed_password.split("$")
        if len(parts) != 3:
            return False
        method, salt_hex, hash_hex = parts
        m_parts = method.split(":")
        algo = m_parts[1]
        iterations = int(m_parts[2])
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)
        derived = hashlib.pbkdf2_hmac(algo, plain_password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(derived, expected_hash)
    except Exception:
        return False


def verify_admin_password(password: str) -> bool:
    """Verify admin login password against configured ADMIN_PASSWORD or ADMIN_PASSWORD_HASH."""
    if settings.ADMIN_PASSWORD_HASH:
        if verify_password(password, settings.ADMIN_PASSWORD_HASH):
            return True
    expected = settings.ADMIN_PASSWORD
    if expected.startswith("pbkdf2:"):
        return verify_password(password, expected)
    # Direct timing-safe comparison with plaintext config
    return hmac.compare_digest(password.encode("utf-8"), expected.encode("utf-8"))


# --- JWT Authentication (HS256 Dual Engine) ---

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _get_jwt_signing_key() -> bytes:
    if settings.JWT_SECRET_KEY:
        return settings.JWT_SECRET_KEY.encode("utf-8")
    return get_master_key()


def create_access_token(data: Dict[str, Any], expires_delta_seconds: Optional[int] = None) -> str:
    """Issue a signed HS256 JWT access token."""
    now = int(time.time())
    ttl = expires_delta_seconds if expires_delta_seconds is not None else (settings.JWT_EXPIRE_MINUTES * 60)
    payload = {**data, "iat": now, "exp": now + ttl}

    try:
        import jwt
        key = _get_jwt_signing_key().decode("latin1", errors="ignore")
        return jwt.encode(payload, key, algorithm="HS256")
    except ImportError:
        # Zero-dependency HS256 JWT encoder
        header = {"alg": "HS256", "typ": "JWT"}
        h_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
        p_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        s1 = _b64url_encode(h_bytes)
        s2 = _b64url_encode(p_bytes)
        signing_input = f"{s1}.{s2}".encode("ascii")
        sig = hmac.new(_get_jwt_signing_key(), signing_input, hashlib.sha256).digest()
        s3 = _b64url_encode(sig)
        return f"{s1}.{s2}.{s3}"


def decode_access_token(token: str) -> Dict[str, Any]:
    """Validate and decode HS256 JWT token. Raises AuthenticationError on failure."""
    try:
        import jwt
        key = _get_jwt_signing_key().decode("latin1", errors="ignore")
        return jwt.decode(token, key, algorithms=["HS256"])
    except ImportError:
        # Zero-dependency HS256 JWT decoder
        parts = token.split(".")
        if len(parts) != 3:
            raise AuthenticationError("Invalid JWT token format")
        h_b64, p_b64, sig_b64 = parts
        signing_input = f"{h_b64}.{p_b64}".encode("ascii")
        expected_sig = hmac.new(_get_jwt_signing_key(), signing_input, hashlib.sha256).digest()
        try:
            actual_sig = _b64url_decode(sig_b64)
        except Exception:
            raise AuthenticationError("Invalid signature encoding in token")
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise AuthenticationError("Invalid token signature")
        try:
            payload = json.loads(_b64url_decode(p_b64).decode("utf-8"))
        except Exception:
            raise AuthenticationError("Malformed token payload")
        now = int(time.time())
        if "exp" in payload and payload["exp"] < now:
            raise AuthenticationError("Authentication token has expired")
        return payload
    except Exception as e:
        raise AuthenticationError(f"Token validation failed: {e}")


async def get_current_admin(
    credentials: Optional[Any] = Security(security_bearer) if security_bearer else None,
) -> Dict[str, Any]:
    """FastAPI Dependency: Authenticate admin user from Bearer token."""
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials if hasattr(credentials, "credentials") else str(credentials)
    try:
        payload = decode_access_token(token)
        if payload.get("role") != "admin":
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions",
            )
        return payload
    except AuthenticationError as e:
        raise HTTPException(
            status_code=401,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )
