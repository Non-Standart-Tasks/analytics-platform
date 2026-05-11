from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class EventIn(BaseModel):
    """Payload, который шлёт telemetry-client (см. backend/packages/telemetry-client)."""

    type: str = Field(..., max_length=64)
    event_id: UUID
    client_timestamp: datetime
    service: str | None = Field(None, max_length=64)
    system_env: str | None = Field(None, max_length=16)
    status: str | None = Field(None, max_length=16)
    request_id: str | None = Field(None, max_length=128)
    data: str | None = Field(None, max_length=2048)

    # Type-специфичные подобъекты. Все опциональные; ровно один должен совпадать с type.
    chat_message: dict[str, Any] | None = None
    llm_tokens: dict[str, Any] | None = None
    crm_task: dict[str, Any] | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        """Собирает все type-специфичные подобъекты в один JSONB."""
        return {
            k: v
            for k, v in {
                "chat_message": self.chat_message,
                "llm_tokens": self.llm_tokens,
                "crm_task": self.crm_task,
                "request": self.request,
                "response": self.response,
            }.items()
            if v is not None
        }
