'''
json 파일에 있는 원본 데이터를 우리 데이터베이스에 채워 넣는 데이터 주입기. 빈 DB에 초기 데이터를 심는역할
'''
import json
import glob
import os
import uuid

from sqlalchemy.orm import Session

from app.database import SessionLocal, Base, engine
from app import models
from app.models import Place, Contest

DATA_DIR = "data"


# ── 작은 헬퍼들 ─────────────────────────────────────
def _clean(v):
    """공백 제거하고, 빈 문자열이면 None으로"""
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _to_float(v):
    """숫자로 변환, 안 되면 None (좌표가 비어있는 경우 대비)"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_iso_date(v):
    """'20260916' → '2026-09-16', 형식 불량이면 None"""
    v = _clean(v)
    if not v or len(v) != 8 or not v.isdigit():
        return None
    return f"{v[:4]}-{v[4:6]}-{v[6:8]}"


# ── 1) 장소 적재: 서울 7개 파일 → places ────────────
def load_places(db: Session) -> int:
    files = glob.glob(os.path.join(DATA_DIR, "서울_*.json"))
    seen = set()   # content_id 중복 방지
    count = 0

    for path in files:
        # 축제 '날짜' 파일은 places가 아니라 contests용이므로 건너뜀
        if os.path.basename(path) == "서울_축제날짜.json":
            continue

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        for it in data.get("items", []):
            cid = _clean(it.get("contentid"))
            if not cid or cid in seen:
                continue          # id 없거나 이미 넣은 건 건너뜀
            seen.add(cid)

            db.add(Place(
                id=str(uuid.uuid4()),
                content_id=cid,
                content_type_id=int(it.get("contenttypeid") or 0),
                title=_clean(it.get("title")) or "(제목 없음)",
                address=_clean(it.get("addr1")),
                first_image_url=_clean(it.get("firstimage")),
                map_x=_to_float(it.get("mapx")),   # 경도
                map_y=_to_float(it.get("mapy")),   # 위도
            ))
            count += 1

    db.commit()
    return count


# ── 2) 축제 적재: 서울_축제날짜.json → contests ─────
def load_contests(db: Session) -> int:
    path = os.path.join(DATA_DIR, "서울_축제날짜.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # content_id → place.id 매핑
    # (서울_축제공연행사.json이 이미 places에 적재돼 있어 연결 가능)
    cid_to_place = {p.content_id: p.id for p in db.query(Place).all()}

    count = 0
    for it in data.get("items", []):
        cid = _clean(it.get("contentid"))
        start = _to_iso_date(it.get("eventstartdate"))
        end = _to_iso_date(it.get("eventenddate"))
        if not start or not end:
            continue          # 날짜 불량 항목 건너뜀

        db.add(Contest(
            id=str(uuid.uuid4()),
            place_id=cid_to_place.get(cid),      # content_id로 정확히 연결
            start_date=start,
            end_date=end,
            title=_clean(it.get("title")) or "(제목 없음)",
            image_url=_clean(it.get("first_image")),   # 축제 이미지도 확보
            description=None,
            age_limit=None,
        ))
        count += 1

    db.commit()
    return count


# ── 전체 적재 (비어 있을 때만) ──────────────────────
def seed_all(reset: bool = False):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if reset:
            db.query(Contest).delete()
            db.query(Place).delete()
            db.commit()

        # 이미 데이터가 있으면 다시 넣지 않음 (중복 방지)
        if db.query(Place).count() == 0:
            n1 = load_places(db)
            n2 = load_contests(db)
            print(f"적재 완료 → places {n1}개, contests {n2}개")
        else:
            print("이미 데이터가 있어 적재를 건너뜁니다.")
    finally:
        db.close()


if __name__ == "__main__":
    # python -m app.seed 로 직접 실행하면 초기화 후 적재
    seed_all(reset=True)