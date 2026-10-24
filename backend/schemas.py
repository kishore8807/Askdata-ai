"""Pydantic request models."""
from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)   # username OR email
    password: str = Field(min_length=1, max_length=256)


class GenerateRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    attachment_id: str | None = Field(default=None, max_length=36)


class ExecuteRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=1_000_000)
    question: str | None = Field(default=None, max_length=1000)
    # Explicit acknowledgement for dangerous DBA operations (DROP / TRUNCATE / no-WHERE writes)
    confirm_dangerous: bool = False


class RejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)



class ApproveRequest(BaseModel):
    confirm_dangerous: bool = False


class MarkReadRequest(BaseModel):
    ids: list[int] | None = None
