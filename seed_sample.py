from app.database import SessionLocal, Base, engine
from app import models
from app.models import Place, Contest
from app.utils import new_uuid

Base.metadata.create_all(bind=engine)

db = SessionLocal()

# 기존 장소/행사 데이터를 비우고 새로 넣는다 (여러 번 실행해도 안전하게)
db.query(Contest).delete()
db.query(Place).delete()
db.commit()

# 샘플 장소
place = Place(
    id=new_uuid(),
    content_id="2556687",
    content_type_id=15,
    title="대학로",
    address="서울특별시 종로구 대학로 104 (동숭동)",
    first_image_url="https://tong.visitkorea.or.kr/cms/resource/47/4077947_image2_1.jpg",
    map_x=127.0023742293,
    map_y=37.580512461,
)
db.add(place)
db.commit()

# 샘플 행사 (위 장소에 연결)
contest = Contest(
    id=new_uuid(),
    place_id=place.id,
    start_date="2026-07-10",
    end_date="2026-07-17",
    title="문학주간 2026",
    image_url="https://example.com/image.jpg",
    description="문학 관련 행사입니다.",
    age_limit=None,
)
db.add(contest)
db.commit()
db.close()

print("샘플 데이터 삽입 완료!")