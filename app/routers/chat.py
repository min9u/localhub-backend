import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ChatRequest
from app.responses import success_response
from app.chatbot import (
    extract_params, run_query, serialize, build_answer, SMALL_TALK_ANSWER,
)

router = APIRouter(prefix="/chat", tags=["chat"])

# uvicorn 콘솔에 바로 찍히도록 uvicorn 로거에 편승 (별도 로깅 설정 불필요)
logger = logging.getLogger("uvicorn.error")


# ── 9. 챗봇 질의응답 ───────────────────────────────
@router.post("")
def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    history = [m.model_dump() for m in payload.history]
    prev_questions = [m["content"] for m in history if m["role"] == "user"]

    try:
        # ① 1차 호출 — 검색 조건 추출 (DB 원본은 전달하지 않음)
        params = extract_params(payload.message, prev_questions)
        logger.info(f"[chat] 질문: {payload.message!r} → 1차 추출: {params}")

        # ②-a 조기 종료 — 잡담·무관 질문은 DB 조회·2차 호출 없이 고정 응답
        if params["intent"] == "none":
            logger.info("[chat] intent=none → DB 조회·2차 호출 생략 (조기 종료)")
            return success_response(
                data={
                    "answer": SMALL_TALK_ANSWER,
                    "debug": {
                        "params": params,
                        "dbSearched": False,      # DB 서칭 단계를 안 거쳤음을 명시
                        "rowCount": 0,
                        "resultPreview": None,
                        "aiCalls": 1,             # 1차 호출만 사용
                    },
                },
                message="챗봇 응답에 성공했습니다. (조기 종료)",
            )

        # ②-b 서버가 템플릿 쿼리에 바인딩·실행 (AI는 SQL에 관여 안 함)
        headers, rows = run_query(db, params)
        result_text = serialize(headers, rows)
        logger.info(f"[chat] DB 조회 실행 → {len(rows)}행 반환")
        logger.info(f"[chat] 2차 호출에 전달되는 결과:\n{result_text}")

        # ③ 2차 호출 — 절단된 결과 + 다이어트된 히스토리로 답변 생성
        answer = build_answer(payload.message, history, result_text)

    except HTTPException:
        raise
    except Exception:
        logger.exception("[chat] 응답 생성 실패")   # 원문 스택은 서버 로그에만
        raise HTTPException(status_code=502, detail="챗봇 응답 생성에 실패했습니다.")

    return success_response(
        data={
            "answer": answer,
            # debug 블록: 시연·개발용. 발표 후 제거하거나 아래 주석처럼 축소
            "debug": {
                "params": params,          # 1차 호출이 해석한 검색 조건
                "dbSearched": True,        # DB 서칭 단계를 거쳤음
                "rowCount": len(rows),     # 조회된 행 수 (0이면 '결과 없음' 답변의 근거)
                "resultPreview": result_text,  # 2차 호출에 실제로 전달된 텍스트 그대로
                "aiCalls": 2,
            },
        },
        message="챗봇 응답에 성공했습니다.",
    )
