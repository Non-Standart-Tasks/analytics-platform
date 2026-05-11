"""JWT-аутентификация с привязкой к проекту.

Каждый клиент (avia, chat, ...) получает свой JWT с claim `project`.
Приёмник:
  1. Валидирует подпись общим секретом (TELEMETRY_JWT_SECRET).
  2. Извлекает `project` из claims.
  3. На уровне POST /events проверяет, что event.service == claims.project
     — это предотвращает кросс-проектные подделки.
"""

import jwt
from fastapi import Header, HTTPException, status

from app.config import settings


class TokenClaims:
    def __init__(self, project: str, raw: dict):
        self.project = project
        self.raw = raw


def verify_jwt(authorization: str = Header(...)) -> TokenClaims:
    """FastAPI dependency: проверяет Bearer-JWT, возвращает claims."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Bearer token")
    token = authorization[7:]
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    project = payload.get("project")
    if not project:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Token missing 'project' claim",
        )
    return TokenClaims(project=project, raw=payload)
