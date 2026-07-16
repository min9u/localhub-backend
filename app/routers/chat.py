import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ChatRequest
from app.responses import success_response
from app.chatbot import (
    extract_plan, execute_plan, serialize_sets, build_answer, SMALL_TALK_ANSWER,
)

router = APIRouter(prefix="/chat", tags=["chat"])

# uvicorn 콘솔에 바로 찍히도록 uvicorn 로거에 편승
logger = logging.getLogger("uvicorn.error")


# ── 9. 챗봇 질의응답 ───────────────────────────────
@router.post("")
def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    history = [m.model_dump() for m in payload.history]
    prev_questions = [m["content"] for m in history if m["role"] == "user"]

    try:
        # ① 1차 호출 — 실행 계획 추출 (DB 원본은 전달하지 않음)
        plan = extract_plan(payload.message, prev_questions)
        logger.info(f"[chat] 질문: {payload.message!r} → 실행 계획: {plan}")

        # ②-a 조기 종료 — 계획이 비어 있으면(잡담·무관) DB 조회·2차 호출 생략
        if not plan["queries"]:
            logger.info("[chat] 빈 계획 → DB 조회·2차 호출 생략 (조기 종료)")
            return success_response(
                data={
                    "answer": SMALL_TALK_ANSWER,
                    "debug": {"plan": plan, "dbSearched": False,
                              "rowCounts": [], "resultPreview": None, "aiCalls": 1},
                },
                message="챗봇 응답에 성공했습니다. (조기 종료)",
            )

        # ②-b 서버가 계획을 순차 실행 (near_prev는 직전 결과 좌표를 이어받음)
        result_sets = execute_plan(db, plan)
        result_text = serialize_sets(result_sets)
        row_counts = [len(rows) for _, rows in result_sets]
        logger.info(f"[chat] 계획 실행 완료 → 세트별 행 수: {row_counts}")
        logger.info(f"[chat] 2차 호출에 전달되는 결과:\n{result_text}")

        # ③ 2차 호출 — 절단된 결과 세트들 + 다이어트된 히스토리로 답변 생성
        answer = build_answer(payload.message, history, result_text)

    except HTTPException:
        raise
    except Exception:
        logger.exception("[chat] 응답 생성 실패")   # 원문 스택은 서버 로그에만
        raise HTTPException(status_code=502, detail="챗봇 응답 생성에 실패했습니다.")

    return success_response(
        data={
            "answer": answer,
            # debug 블록: 시연·개발용. 발표 후 제거 가능
            "debug": {
                "plan": plan,                  # 1차 호출이 수립한 실행 계획
                "dbSearched": True,
                "rowCounts": row_counts,       # 쿼리별 조회 행 수
                "resultPreview": result_text,  # 2차 호출에 전달된 텍스트 그대로
                "aiCalls": 2,
            },
        },
        message="챗봇 응답에 성공했습니다.",
    )
