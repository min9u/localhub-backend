import calendar
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Place, Contest
from app.responses import success_response

router = APIRouter(tags=["locations"])


def place_to_dict(place):
    """Place 객체 → 명세의 camelCase 형식"""
    return {
        "id": place.id,
        "contentId": place.content_id,
        "contentTypeId": place.content_type_id,
        "title": place.title,
        "address": place.address,
        "firstImageUrl": place.first_image_url,
        "mapX": place.map_x,
        "mapY": place.map_y,
    }


# ── 7. 장소 목록 조회 ──────────────────────────────
@router.get("/places")
def list_places(db: Session = Depends(get_db)):
    places = db.query(Place).order_by(Place.title).all()
    items = [place_to_dict(p) for p in places]
    return success_response(
        data={"items": items},
        message="장소 목록 조회에 성공했습니다.",
    )


def nested_place_dict(place):
    """캘린더 응답 안에 들어갈 축약된 place 정보 (없으면 None)"""
    if place is None:
        return None
    return {
        "id": place.id,
        "title": place.title,
        "address": place.address,
        "mapX": place.map_x,
        "mapY": place.map_y,
    }


# ── 8. 월별 캘린더 조회 ────────────────────────────
@router.get("/calendars")
def get_calendar(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
):
    # 해당 월의 첫날과 마지막날을 문자열로 만든다
    last_day = calendar.monthrange(year, month)[1]   # 그 달의 마지막 날짜 (예: 31)
    month_start = f"{year:04d}-{month:02d}-01"        # "2026-07-01"
    month_end = f"{year:04d}-{month:02d}-{last_day:02d}"  # "2026-07-31"

    # 행사 기간이 이 달과 조금이라도 겹치는 것들을 모두 가져온다
    contests = (
        db.query(Contest)
        .filter(Contest.start_date <= month_end, Contest.end_date >= month_start)
        .order_by(Contest.start_date)
        .all()
    )

    items = []
    for c in contests:
        items.append({
            "id": c.id,
            "title": c.title,
            "startDate": c.start_date,
            "endDate": c.end_date,
            "imageUrl": c.image_url,
            "description": c.description,
            "ageLimit": c.age_limit,
            "address" : c.place.address,
            "mapX": c.place.map_x,
            "mapY" : c.place.map_y
        })

    return success_response(
        data={"year": year, "month": month, "items": items},
        message="캘린더 조회에 성공했습니다.",
    )