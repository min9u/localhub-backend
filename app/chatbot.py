"""
챗봇 로직 v5 (Two-call RAG-lite / 쿼리 계획 방식)

구조 변화:
  이전(intent 방식): 1차 호출이 의도 '하나'를 고르면 템플릿 쿼리 1개에 1:1 대응
  현재(계획 방식):   1차 호출이 '미니 실행 계획'(쿼리 최대 2개 + 의존 관계)을 출력,
                     서버가 순서대로 실행. 복합 질문("9월 축제랑 근처 맛집")까지 커버.

흐름:
  1차 호출  extract_plan()   질문 → {"queries": [...]} 실행 계획 (DB 원본 0건 전달)
  서버      execute_plan()   계획을 ORM 템플릿에 바인딩해 순차 실행 (AI는 SQL에 관여 안 함)
  2차 호출  build_answer()   절단·직렬화된 결과 세트들 + 히스토리 → 최종 답변

데이터 양 통제:
  - 1차: 스키마 요약 몇 줄만 → 입력 상수
  - 결과: 쿼리 1개면 10행, 2개면 각 5행 (합계 10행 고정 → 예산 불변)
  - 셀 120자 절단, 파이프 직렬화, 히스토리 6개·200자
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

MAX_TOTAL_ROWS = 10         # 결과 행 합계 상한 (쿼리 2개면 각 5행)
MAX_CELL = 120              # 셀 문자 상한
MAX_HISTORY = 6             # 히스토리 개수 상한
MAX_ASSISTANT_CHARS = 200   # 과거 assistant 답변 절단 길이
MAX_QUERIES = 2             # 실행 계획의 쿼리 개수 상한

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# content_type_id 공식 매핑 (한국관광공사 표준 8종)
PLACE_TYPE_MAP = {
    "관광지": 12, "문화시설": 14, "축제공연행사": 15, "여행코스": 25,
    "레포츠": 28, "숙박": 32, "쇼핑": 38, "음식점": 39,
}

SMALL_TALK_ANSWER = (
    "안녕하세요! 지역 축제·관광 가이드예요. "
    "축제 일정이나 가볼 만한 곳을 물어봐 주세요. 예) \"10월에 하는 축제 알려줘\""
)


# ── 1차 호출: 실행 계획 추출 ────────────────────────
# 고정 프롬프트는 항상 동일 문자열로 맨 앞 배치 (캐싱 보존). 오늘 날짜는 user 메시지로.
EXTRACT_SYSTEM = """당신은 지역 축제·관광 가이드 챗봇의 검색 계획 수립기입니다.
아래 JSON 형식으로만 응답하세요. 다른 텍스트·설명·마크다운 금지.

{"queries": [ 최대 2개의 쿼리 객체 ]}

쿼리 객체 형식:
{"table": "contests" | "places",
 "date_from": "YYYY-MM-DD" 또는 null (contests 전용),
 "date_to": "YYYY-MM-DD" 또는 null (contests 전용),
 "month": 1~12 또는 null (contests 전용),
 "keyword": 검색어 또는 null,
 "place_type": 8개 유형 중 하나 또는 null (places 전용),
 "sort": "start_date" | "end_date" | null (contests 전용),
 "near_prev": true | false (places 전용 — 직전 쿼리 결과 위치 근처에서 검색),
 "limit": 1~10 (기본 5)}

place_type 후보: "관광지" "문화시설" "축제공연행사" "여행코스" "레포츠" "숙박" "쇼핑" "음식점"

계획 수립 규칙:
- 질문에 필요한 테이블만 요청. 확실하지 않으면 1개만. (불필요한 쿼리는 답변 품질을 해침)
- 축제·행사 일정 질문 → contests 1개
- 장소 질문 → places 1개
- "X 근처 Y" 형태 → 쿼리 2개:
  1번째 = 기준 위치 탐색 (X가 축제면 contests, 장소면 places. keyword는 핵심 고유명사만:
    "서울남산축제" → "남산", "경복궁" → "경복궁")
  2번째 = places, near_prev=true, place_type=찾을 유형
- 복합 질문("9월 축제랑 그 근처 맛집도") → contests + places(near_prev=true) 2개
- 축제·관광과 무관한 질문(인사·잡담) → {"queries": []}
- 유의어 정규화: 맛집·식당·카페→"음식점", 호텔·숙소→"숙박",
  박물관·미술관·공연장→"문화시설", 공원·명소→"관광지"

날짜 규칙 (user 메시지의 '오늘 날짜' 기준으로 계산):
- "9월 둘째 주" "이번 주말" "9월 15일부터 20일까지" 등 구체적 기간 → date_from/date_to
- "9월에" 같은 월 단위 → month만
- 날짜 언급 없으면 셋 다 null

데이터 요약:
contests(title, start_date 'YYYY-MM-DD', end_date, description, age_limit, 장소명, 주소)
  예시 행: 서울국제작가축제 | 2026-09-11 | 2026-09-16 | 개막식 및 대담... | 전 연령
places(title, address, 유형=위 8종, 좌표 보유)
  예시 행: 양화한강공원 | 서울특별시 영등포구 노들로 221 | 관광지"""


def _valid_date(s) -> str | None:
    if not isinstance(s, str) or not DATE_RE.match(s):
        return None
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return s
    except ValueError:
        return None


def _validate_query(q: dict, per_query_cap: int, is_first: bool) -> dict | None:
    """쿼리 객체 1개를 검증·클램핑. 신뢰 경계는 항상 서버."""
    if not isinstance(q, dict) or q.get("table") not in ("contests", "places"):
        return None
    v = {"table": q["table"]}
    v["keyword"] = q.get("keyword") if isinstance(q.get("keyword"), str) and q.get("keyword").strip() else None
    limit = q.get("limit")
    v["limit"] = min(max(limit, 1), per_query_cap) if isinstance(limit, int) else min(5, per_query_cap)

    if v["table"] == "contests":
        month = q.get("month")
        v["month"] = month if isinstance(month, int) and 1 <= month <= 12 else None
        v["sort"] = q.get("sort") if q.get("sort") in ("start_date", "end_date") else None
        v["date_from"], v["date_to"] = _valid_date(q.get("date_from")), _valid_date(q.get("date_to"))
        if v["date_from"] and v["date_to"] and v["date_from"] > v["date_to"]:
            v["date_from"], v["date_to"] = v["date_to"], v["date_from"]
    else:  # places
        v["place_type"] = q.get("place_type") if q.get("place_type") in PLACE_TYPE_MAP else None
        v["near_prev"] = bool(q.get("near_prev")) and not is_first   # 첫 쿼리엔 '직전'이 없음
    return v


def extract_plan(question: str, prev_questions: list[str]) -> dict:
    """1차 호출. 질문 → 실행 계획. DB 규모와 무관하게 입력 상수."""
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
        max_tokens=250,   # 쿼리 2개 JSON까지 커버
    )
    try:
        raw = json.loads(resp.choices[0].message.content.strip())
        raw_queries = raw.get("queries", [])
        assert isinstance(raw_queries, list)
    except (json.JSONDecodeError, AttributeError, AssertionError):
        return {"queries": []}   # 파싱 실패 → 조기 종료 경로

    raw_queries = raw_queries[:MAX_QUERIES]
    # 동적 캡: 쿼리 1개면 10행, 2개면 각 5행 → 합계 10행 고정 (2차 호출 예산 불변)
    per_cap = MAX_TOTAL_ROWS // max(len(raw_queries), 1)
    queries = []
    for i, rq in enumerate(raw_queries):
        v = _validate_query(rq, per_cap, is_first=(i == 0))
        if v:
            queries.append(v)
    return {"queries": queries}


# ── 서버: 실행 계획 수행 (ORM 바인딩 → 인젝션 원천 차단) ──
def _strip_fillers(keyword: str) -> str:
    s = keyword
    for f in ("서울", "축제", "페스티벌", "행사"):
        s = s.replace(f, "")
    return s.strip()


def _resolve_range(q: dict) -> tuple[str | None, str | None]:
    """contests 검색 구간 확정: date_from/date_to 우선, 없으면 month → 이번 연도 구간."""
    if q.get("date_from") or q.get("date_to"):
        return (q.get("date_from") or q.get("date_to"),
                q.get("date_to") or q.get("date_from"))
    if q.get("month"):
        year = datetime.now(KST).year
        last_day = calendar.monthrange(year, q["month"])[1]
        return (f"{year:04d}-{q['month']:02d}-01",
                f"{year:04d}-{q['month']:02d}-{last_day:02d}")
    return None, None


def _query_contests(db: Session, q: dict):
    """contests 템플릿. 반환: (헤더, 행, 앵커좌표 또는 None)"""
    query = db.query(Contest).options(joinedload(Contest.place))
    range_start, range_end = _resolve_range(q)
    if range_start:
        query = query.filter(Contest.start_date <= range_end,
                             Contest.end_date >= range_start)
    if q["keyword"]:
        query = query.filter(Contest.title.contains(q["keyword"]))

    # 기본 정렬: 기간 짧은 순 → 연중 상설 행사가 단기 축제를 밀어내지 못하게
    duration = func.julianday(Contest.end_date) - func.julianday(Contest.start_date)
    if q["sort"]:
        query = query.order_by(getattr(Contest, q["sort"]), duration)
    else:
        query = query.order_by(duration, Contest.start_date)
    contests = query.limit(q["limit"]).all()

    headers = ["축제명", "시작일", "종료일", "소개", "연령제한", "장소", "주소"]
    rows = [
        (c.title, c.start_date, c.end_date,
         c.description or "", c.age_limit or "제한 없음",
         c.place.title if c.place else "장소 미정",
         c.place.address if c.place else "")
        for c in contests
    ]
    # 앵커 = 첫 결과의 장소 좌표 (다음 쿼리의 near_prev가 사용)
    anchor = None
    for c in contests:
        if c.place and c.place.map_x is not None:
            anchor = (c.place.title, c.place.map_x, c.place.map_y)
            break
    return headers, rows, anchor


def _query_places(db: Session, q: dict, anchor):
    """places 템플릿. anchor가 있으면 거리순 근처 검색. 반환: (헤더, 행, 앵커좌표)"""
    type_id = PLACE_TYPE_MAP.get(q.get("place_type") or "")

    if anchor:
        a_title, ax, ay = anchor
        # 서울 위도 기준 근사: 경도 1도≈88km, 위도 1도≈111km
        d2 = (((Place.map_x - ax) * 88.0) * ((Place.map_x - ax) * 88.0)
              + ((Place.map_y - ay) * 111.0) * ((Place.map_y - ay) * 111.0))
        query = (db.query(Place.title, Place.address, Place.map_x, Place.map_y)
                 .filter(Place.map_x.isnot(None), Place.title != a_title))
        if type_id:
            query = query.filter(Place.content_type_id == type_id)
        if q["keyword"]:
            query = query.filter(or_(Place.title.contains(q["keyword"]),
                                     Place.address.contains(q["keyword"])))
        found = query.order_by(d2).limit(q["limit"]).all()
        headers = ["장소명", "주소", f"'{a_title}'까지 거리(km)"]  # 앵커명은 헤더에 1회만
        rows = [
            (t, addr,
             f"{(((mx - ax) * 88.0) ** 2 + ((my - ay) * 111.0) ** 2) ** 0.5:.1f}")
            for t, addr, mx, my in found
        ]
        return headers, rows, anchor

    query = db.query(Place.title, Place.address, Place.map_x, Place.map_y)
    if type_id:
        query = query.filter(Place.content_type_id == type_id)
    if q["keyword"]:
        query = query.filter(or_(Place.title.contains(q["keyword"]),
                                 Place.address.contains(q["keyword"])))
    found = query.order_by(Place.title).limit(q["limit"]).all()
    headers = ["장소명", "주소"]
    rows = [(t, addr) for t, addr, _, _ in found]
    new_anchor = (found[0][0], found[0][2], found[0][3]) if found and found[0][2] is not None else None
    return headers, rows, new_anchor


def execute_plan(db: Session, plan: dict) -> list[tuple[list[str], list[tuple]]]:
    """실행 계획을 순차 수행. near_prev는 직전 쿼리의 앵커 좌표를 이어받음."""
    result_sets = []
    anchor = None
    queries = plan["queries"]

    for i, q in enumerate(queries):
        if q["table"] == "contests":
            headers, rows, new_anchor = _query_contests(db, q)
            # 앵커 역할인데 못 찾음 → 군더더기 단어 떼고 1회 재시도 ("서울남산축제"→"남산")
            if (not rows and q["keyword"]
                    and i + 1 < len(queries) and queries[i + 1].get("near_prev")):
                retry = dict(q, keyword=_strip_fillers(q["keyword"]))
                if retry["keyword"] and retry["keyword"] != q["keyword"]:
                    headers, rows, new_anchor = _query_contests(db, retry)
            if new_anchor:
                anchor = new_anchor
        else:
            use_anchor = anchor if q.get("near_prev") else None
            headers, rows, new_anchor = _query_places(db, q, use_anchor)
            if (not rows and q["keyword"] and not use_anchor
                    and i + 1 < len(queries) and queries[i + 1].get("near_prev")):
                retry = dict(q, keyword=_strip_fillers(q["keyword"]))
                if retry["keyword"] and retry["keyword"] != q["keyword"]:
                    headers, rows, new_anchor = _query_places(db, retry, None)
            if new_anchor and not q.get("near_prev"):
                anchor = new_anchor

        result_sets.append((headers, rows))
    return result_sets


def serialize_sets(result_sets: list[tuple[list[str], list[tuple]]]) -> str:
    """결과 세트들을 구획 지어 파이프 직렬화. 셀 120자 절단, 개행 치환."""
    if not result_sets or all(not rows for _, rows in result_sets):
        return "(조회된 데이터 없음)"
    blocks = []
    for idx, (headers, rows) in enumerate(result_sets, 1):
        title = f"[결과 {idx}]" if len(result_sets) > 1 else "[결과]"
        if not rows:
            blocks.append(f"{title}\n(조회된 데이터 없음)")
            continue
        lines = [title, "|".join(headers)]
        for row in rows:
            lines.append("|".join(str(c or "").replace("\n", " ")[:MAX_CELL] for c in row))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ── 2차 호출: 답변 생성 ─────────────────────────────
ANSWER_SYSTEM = """당신은 친절한 지역 축제·관광 가이드입니다.
[조회 결과]에 있는 내용만 근거로, 한국어 3~5문장으로 간결하게 답하세요.
결과가 여러 구획이면 자연스럽게 이어서 설명하세요 (예: 축제 소개 후 근처 장소 안내).
축제는 시작일~종료일 기간을 함께 알려주고, 연령제한 정보가 있으면 자연스럽게 포함하세요.
결과가 비어 있으면 찾지 못했다고 솔직히 말하고, 추측으로 채우지 마세요."""


def trim_history(history: list[dict]) -> list[dict]:
    trimmed = []
    for m in history[-MAX_HISTORY:]:
        content = m["content"]
        if m["role"] == "assistant":
            content = content[:MAX_ASSISTANT_CHARS]
        trimmed.append({"role": m["role"], "content": content})
    return trimmed


def build_answer(question: str, history: list[dict], result_text: str) -> str:
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
