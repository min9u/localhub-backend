"""
챗봇 로직 (Two-call RAG-lite / 파라미터 추출 방식)

흐름:
  1차 호출  extract_params()  질문 → 검색 조건 JSON (DB 원본 0건 전달)
  서버      run_query()       조건을 ORM 템플릿 쿼리에 바인딩 (AI는 SQL에 관여 안 함)
  2차 호출  build_answer()    절단·직렬화된 결과 + 히스토리 → 최종 답변

데이터 양 통제:
  - 1차: 스키마 요약 몇 줄만 (테이블 원본 X) → 입력 약 300토큰 상수
  - 결과: 최대 10행, 셀 120자 절단, 파이프 구분 직렬화
  - 히스토리: 최근 6개, 과거 assistant 답변은 앞 200자만
"""
import calendar
import json
from datetime import datetime

from openai import OpenAI
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models import Contest, Place
from app.utils import KST

client = OpenAI(api_key=settings.openai_api_key)

MAX_ROWS = 10               # 결과 행 상한
MAX_CELL = 120              # 셀 문자 상한
MAX_HISTORY = 6             # 히스토리 개수 상한
MAX_ASSISTANT_CHARS = 200   # 과거 assistant 답변 절단 길이

# content_type_id 매핑 — seed 데이터의 실제 코드값 확인 후 조정
PLACE_TYPE_MAP = {"관광지": 12, "문화시설": 14, "맛집": 39}

# intent=none 조기 종료 시 고정 응답 (AI 호출 1회로 턴 종료)
SMALL_TALK_ANSWER = (
    "안녕하세요! 지역 축제·관광 가이드예요. "
    "축제 일정이나 가볼 만한 곳을 물어봐 주세요. 예) \"10월에 하는 축제 알려줘\""
)


# ── 1차 호출: 파라미터 추출 ─────────────────────────
# 고정 프롬프트는 항상 동일한 문자열로 맨 앞 배치 (OpenAI 프롬프트 캐싱 할인 대상)
EXTRACT_SYSTEM = """당신은 지역 축제·관광 가이드 챗봇의 검색 조건 추출기입니다.
아래 JSON 형식으로만 응답하세요. 다른 텍스트·설명·마크다운 금지.

{"intent": "contest_search" | "place_search" | "none",
 "month": 1~12 또는 null,
 "keyword": 검색어 문자열 또는 null,
 "place_type": "관광지" | "문화시설" | "맛집" | null,
 "sort": "start_date" | "end_date" | null,
 "limit": 1~10 (기본 5)}

규칙:
- 축제·행사·공연 질문이면 intent=contest_search
- 장소(관광지·맛집·문화시설) 질문이면 intent=place_search
- 축제·관광과 무관한 질문(인사·잡담 포함)이면 intent=none, 나머지 필드 null
- "가장 늦게 끝나는" → sort=end_date

데이터 요약:
contests(title, start_date 'YYYY-MM-DD', end_date, 장소명, 주소)
  예시 행: 서울세계불꽃축제 | 2026-10-03 | 2026-10-03 | 여의도한강공원
places(title, address, 유형=관광지/문화시설/맛집)
  예시 행: 경복궁 | 서울 종로구 사직로 161 | 관광지"""


def extract_params(question: str, prev_questions: list[str]) -> dict:
    """1차 호출. DB 규모와 무관하게 입력이 상수로 고정됨."""
    context = "\n".join(f"직전 질문: {q}" for q in prev_questions[-2:])
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": f"{context}\n현재 질문: {question}".strip()},
        ],
        temperature=0,
        max_tokens=150,   # 파라미터 JSON만 받으면 되므로 낭비 차단
    )
    try:
        params = json.loads(resp.choices[0].message.content.strip())
    except (json.JSONDecodeError, AttributeError):
        return {"intent": "none"}   # 파싱 실패 → 조기 종료 경로로 폴백

    # 서버 측 타입 검증·클램핑 — 신뢰 경계는 항상 서버
    if params.get("intent") not in ("contest_search", "place_search"):
        return {"intent": "none"}
    month = params.get("month")
    params["month"] = month if isinstance(month, int) and 1 <= month <= 12 else None
    params["sort"] = params.get("sort") if params.get("sort") in ("start_date", "end_date") else "start_date"
    limit = params.get("limit")
    params["limit"] = min(max(limit, 1), MAX_ROWS) if isinstance(limit, int) else 5
    return params


# ── 서버: 템플릿 쿼리 (ORM 바인딩 → 인젝션 원천 차단) ──
def run_query(db: Session, p: dict) -> tuple[list[str], list[tuple]]:
    """파라미터를 고정 쿼리 골격에 꽂아 실행. 반환: (헤더, 행 목록)"""
    if p["intent"] == "contest_search":
        q = db.query(Contest).options(joinedload(Contest.place))
        if p["month"]:
            # 캘린더 API(routers/places.py)와 동일한 '기간 겹침' 방식
            year = datetime.now(KST).year
            last_day = calendar.monthrange(year, p["month"])[1]
            month_start = f"{year:04d}-{p['month']:02d}-01"
            month_end = f"{year:04d}-{p['month']:02d}-{last_day:02d}"
            q = q.filter(Contest.start_date <= month_end,
                         Contest.end_date >= month_start)
        if p.get("keyword"):
            q = q.filter(Contest.title.contains(p["keyword"]))
        contests = q.order_by(getattr(Contest, p["sort"])).limit(p["limit"]).all()

        headers = ["축제명", "시작일", "종료일", "장소", "주소"]
        rows = [
            (c.title, c.start_date, c.end_date,
             c.place.title if c.place else "장소 미정",   # place_id는 SET NULL 가능
             c.place.address if c.place else "")
            for c in contests
        ]

    else:  # place_search
        q = db.query(Place.title, Place.address)
        type_id = PLACE_TYPE_MAP.get(p.get("place_type") or "")
        if type_id:
            q = q.filter(Place.content_type_id == type_id)
        if p.get("keyword"):
            q = q.filter(or_(Place.title.contains(p["keyword"]),
                             Place.address.contains(p["keyword"])))
        headers = ["장소명", "주소"]
        rows = q.order_by(Place.title).limit(p["limit"]).all()

    return headers, rows


def serialize(headers: list[str], rows: list[tuple]) -> str:
    """파이프 구분 직렬화 — JSON 대비 토큰 30~50% 절약. 셀 120자 절단."""
    if not rows:
        return "(조회된 데이터 없음)"
    lines = ["|".join(headers)]
    for row in rows:
        lines.append("|".join(str(c or "")[:MAX_CELL] for c in row))
    return "\n".join(lines)


# ── 2차 호출: 답변 생성 ─────────────────────────────
ANSWER_SYSTEM = """당신은 친절한 지역 축제·관광 가이드입니다.
[조회 결과]에 있는 내용만 근거로, 한국어 3~5문장으로 간결하게 답하세요.
결과가 비어 있으면 찾지 못했다고 솔직히 말하고, 추측으로 채우지 마세요."""


def trim_history(history: list[dict]) -> list[dict]:
    """최근 6개만, 과거 assistant 답변은 앞 200자만 — 히스토리 다이어트."""
    trimmed = []
    for m in history[-MAX_HISTORY:]:
        content = m["content"]
        if m["role"] == "assistant":
            content = content[:MAX_ASSISTANT_CHARS]
        trimmed.append({"role": m["role"], "content": content})
    return trimmed


def build_answer(question: str, history: list[dict], result_text: str) -> str:
    """2차 호출. 결과 절단 + 히스토리 다이어트로 입력 상한 약 1,000토큰."""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM},   # 고정 → 캐싱 대상
            *trim_history(history),
            {"role": "user", "content": f"[조회 결과]\n{result_text}\n\n[질문]\n{question}"},
        ],
        temperature=0.3,
        max_tokens=500,
    )
    return resp.choices[0].message.content.strip()
