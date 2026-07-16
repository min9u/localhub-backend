from pydantic import BaseModel, Field

from typing import Literal


class PostCreate(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    pwd: str = Field(min_length=1)


class PostUpdate(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    pwd: str = Field(min_length=1)

    # ── 챗봇 (추가) ────────────────────────────────────
class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]   # 그 외 값은 422로 자동 거절
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    # 무상태 서버 — 대화 히스토리는 프론트가 보관해서 매 요청에 실어 보냄
    history: list[ChatMessage] = []