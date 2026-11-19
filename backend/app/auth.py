"""Senhas (bcrypt) e tokens JWT (HS256, 12 h)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

TOKEN_TTL = timedelta(hours=12)
ALGORITHM = "HS256"
BCRYPT_ROUNDS = 12
MIN_PASSWORD = 8
MAX_PASSWORD_BYTES = 72  # limite do bcrypt

# hash fixo para gastar o mesmo tempo quando o email não existe (evita enumeração por tempo)
_DUMMY_HASH: bytes | None = None


class AuthError(Exception):
    """Token ausente, inválido ou expirado."""


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"A senha deve ter pelo menos {MIN_PASSWORD} caracteres."
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return f"A senha deve ter no máximo {MAX_PASSWORD_BYTES} bytes."
    return None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    global _DUMMY_HASH
    raw = password.encode()[:MAX_PASSWORD_BYTES]
    if not password_hash:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(rounds=BCRYPT_ROUNDS))
        bcrypt.checkpw(raw, _DUMMY_HASH)
        return False
    try:
        return bcrypt.checkpw(raw, password_hash.encode())
    except ValueError:
        return False


def create_token(user_id: str, org_id: str, secret: str, ttl: timedelta = TOKEN_TTL) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "org": org_id, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Sessão expirada. Faça login novamente.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Token inválido.") from exc
    if not isinstance(payload.get("sub"), str) or not isinstance(payload.get("org"), str):
        raise AuthError("Token inválido.")
    return payload
