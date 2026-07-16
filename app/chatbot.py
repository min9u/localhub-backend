"""
챗봇 로직 (Two-call RAG-lite / 파라미터 추출 방식)

흐름:
  1차 호출  extract_params()  질문 → 검색 조건 JSON (DB 원본 0건 전달)
  서버      run_query()       조건을 ORM 템플릿 쿼리에 바인딩 (AI는 SQL에 관여 안 함)
  2차 호출  build_answer()    절단·직렬화된 결과 + 히스토리 → 최종 답변

데이터 양 통제:
  - 1차: 스키마 요약 몇 줄만 (테이블 원본 X) → 입력 상수
  - 결과: 최대 10행, 셀 120자 절단, 파이프 구분 직렬화
  - 히스토리: 최근 6개, 과거 assistant 답변은 앞 200자만

날짜 처리 (v3):
  - "9월 둘째 주", "이번 주말" 같은 자연어 날짜는 1차 호출이
    date_from/date_to (YYYY-MM-DD 구간)로 변환. 오늘 날짜는 user 메시지로
    전달해 시스템 프롬프트를 고정 유지 (캐싱 보존).
  - 정렬은 '행사 기간 짧은 순' 우선 → 연중 상설 행사가 단기 축제를
    밀어내는 문제 해결.
"""
import calendar
import json
import re
from datetime import datetime

from openai import OpenAI
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models import Contest, Place
from app.utils import KST

client = OpenAI(api_key=settings.openai_api_key)

MAX_ROWS = 10               # 결과 행 상한
MAX_CELL = 120              # 셀 문자 상한 (description도 여기서 절단됨)
MAX_HISTORY = 6             # 히스토리 개수 상한
MAX_ASSISTANT_CHARS = 200   # 과거 assistant 답변 절단 길이

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")   # 날짜 형식 검증용

# content_type_id 공식 매핑 (한국관광공사 표준 8종)
PLACE_TYPE_MAP = {
    "관광지": 12,
    "문화시설": 14,
    "축제공연행사": 15,
    "여행코스": 25,
    "레포츠": 28,
    "숙박": 32,
    "쇼핑": 38,
    "음식점": 39,
}

# intent=none 조기 종료 시 고정 응답 (AI 호출 1회로 턴 종료)
SMALL_TALK_ANSWER = (
    "안녕하세요! 지역 축제·관광 가이드예요. "
    "축제 일정이나 가볼 만한 곳을 물어봐 주세요. 예) \"10월에 하는 축제 알려줘\""
)


# ── 1차 호출: 파라미터 추출 ─────────────────────────
# 고정 프롬프트는 항상 동일한 문자열로 맨 앞 배치 (OpenAI 프롬프트 캐싱 할인 대상)
# 주의: 오늘 날짜는 여기 넣지 말 것 — 매일 프롬프트가 바뀌면 캐싱이 깨짐. user 메시지로 전달.
EXTRACT_SYSTEM = """당신은 지역 축제·관광 가이드 챗봇의 검색 조건 추출기입니다.
아래 JSON 형식으로만 응답하세요. 다른 텍스트·설명·마크다운 금지.

{"intent": "contest_search" | "place_search" | "none",
 "date_from": "YYYY-MM-DD" 또는 null,
 "date_to": "YYYY-MM-DD" 또는 null,
 "month": 1~12 또는 null,
 "keyword": 검색어 문자열 또는 null,
 "place_type": "관광지" | "문화시설" | "축제공연행사" | "여행코스" | "레포츠" | "숙박" | "쇼핑" | "음식점" | null,
 "sort": "start_date" | "end_date" | null,
 "limit": 1~10 (기본 5)}

날짜 규칙 (user 메시지의 '오늘 날짜'를 기준으로 계산):
- 구체적 기간 표현은 date_from/date_to로 변환:
  "9월 둘째 주" → 해당 주 월~일 / "이번 주말" → 오는 토~일 /
  "다음 주" → 다음 월~일 / "9월 15일부터 20일까지" → 그대로
- "9월에", "10월 중" 같은 월 단위 표현은 month만 채우고 date_from/date_to는 null
- 날짜 언급이 없으면 셋 다 null

기타 규칙:
- 축제·행사·공연의 일정 질문이면 intent=contest_search
- 장소(관광지·맛집·숙소 등 위 8개 유형) 질문이면 intent=place_search
- 유의어는 place_type 후보로 정규화: 맛집·식당·카페 → "음식점", 호텔·숙소 → "숙박",
  박물관·미술관·공연장 → "문화시설", 공원·명소 → "관광지"
- 축제·관광과 무관한 질문(인사·잡담 포함)이면 intent=none, 나머지 필드 null
- "가장 늦게 끝나는" → sort=end_date

데이터 요약:
contests(title, start_date 'YYYY-MM-DD', end_date, description, age_limit, 장소명, 주소)
  예시 행: 서울국제작가축제 | 2026-09-11 | 2026-09-16 | 개막식 및 대담... | 전 연령
places(title, address, 유형=위 8종)
  예시 행: 양화한강공원 | 서울특별시 영등포구 노들로 221 | 관광지"""


def _valid_date(s) -> str | None:
    """YYYY-MM-DD 형식 + 실존 날짜인지 검증. 아니면 None."""
    if not isinstance(s, str) or not DATE_RE.match(s):
        return None
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except ValueError:
        return None


def extract_params(question: str, prev_questions: list[str]) -> dict:
    """1차 호출. DB 규모와 무관하게 입력이 상수로 고정됨."""
    today = datetime.now(KST)
    context = "\n".join(f"직전 질문: {q}" for q in prev_questions[-2:])
    user_msg = (
        f"오늘 날짜: {today.strftime('%Y-%m-%d')} ({'월화수목금토일'[today.weekday()]}요일)\n"
        f"{context}\n현재 질문: {question}"
    ).strip()

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": user_msg},
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
    params["sort"] = params.get("sort") if params.get("sort") in ("start_date", "end_date") else None
    limit = params.get("limit")
    params["limit"] = min(max(limit, 1), MAX_ROWS) if isinstance(limit, int) else 5
    # place_type 화이트리스트 검증 — 매핑에 없는 값은 무시(전체 유형 검색)
    if params.get("place_type") not in PLACE_TYPE_MAP:
        params["place_type"] = None
    # 날짜 형식 검증 + from > to 뒤집힘 교정
    params["date_from"] = _valid_date(params.get("date_from"))
    params["date_to"] = _valid_date(params.get("date_to"))
    if params["date_from"] and params["date_to"] and params["date_from"] > params["date_to"]:
        params["date_from"], params["date_to"] = params["date_to"], params["date_from"]
    return params


# ── 서버: 템플릿 쿼리 (ORM 바인딩 → 인젝션 원천 차단) ──
def _resolve_range(p: dict) -> tuple[str | None, str | None]:
    """검색 구간 확정: date_from/date_to 우선, 없으면 month를 이번 연도 구간으로 변환."""
    if p.get("date_from") or p.get("date_to"):
        # 한쪽만 있으면 그 날 하루로 취급 ("9월 18일에 뭐 해?" 같은 단일 날짜 질문)
        return (p.get("date_from") or p.get("date_to"),
                p.get("date_to") or p.get("date_from"))
    if p.get("month"):
        year = datetime.now(KST).year
        last_day = calendar.monthrange(year, p["month"])[1]
        return (f"{year:04d}-{p['month']:02d}-01",
                f"{year:04d}-{p['month']:02d}-{last_day:02d}")
    return None, None


def run_query(db: Session, p: dict) -> tuple[list[str], list[tuple]]:
    """파라미터를 고정 쿼리 골격에 꽂아 실행. 반환: (헤더, 행 목록)"""
    if p["intent"] == "contest_search":
        q = db.query(Contest).options(joinedload(Contest.place))

        range_start, range_end = _resolve_range(p)
        if range_start:
            # 캘린더 API(routers/places.py)와 동일한 '기간 겹침' 방식
            q = q.filter(Contest.start_date <= range_end,
                         Contest.end_date >= range_start)
        if p.get("keyword"):
            q = q.filter(Contest.title.contains(p["keyword"]))

        # 정렬: 유저가 명시한 기준이 있으면 그것부터, 기본은 '기간 짧은 순'
        # → 연중 상설 행사(1/1~12/31)가 단기 축제를 밀어내지 못하게 함
        duration = func.julianday(Contest.end_date) - func.julianday(Contest.start_date)
        if p.get("sort"):
            q = q.order_by(getattr(Contest, p["sort"]), duration)
        else:
            q = q.order_by(duration, Contest.start_date)
        contests = q.limit(p["limit"]).all()

        headers = ["축제명", "시작일", "종료일", "소개", "연령제한", "장소", "주소"]
        rows = [
            (c.title, c.start_date, c.end_date,
             c.description or "",                       # 소개 — serialize에서 120자 절단
             c.age_limit or "제한 없음",                 # 연령제한 (DB상 문자열 값)
             c.place.title if c.place else "장소 미정",  # place_id는 SET NULL 가능
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
        # description의 개행이 행 구조를 깨지 않도록 공백으로 치환 후 절단
        lines.append("|".join(str(c or "").replace("\n", " ")[:MAX_CELL] for c in row))
    return "\n".join(lines)


# ── 2차 호출: 답변 생성 ─────────────────────────────
ANSWER_SYSTEM = """당신은 친절한 지역 축제·관광 가이드입니다.
[조회 결과]에 있는 내용만 근거로, 한국어 3~5문장으로 간결하게 답하세요.
축제는 시작일~종료일 기간을 함께 알려주고, 연령제한 정보가 있으면 자연스럽게 포함하세요.
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
    """2차 호출. 결과 절단 + 히스토리 다이어트로 입력 상한 유지."""
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
