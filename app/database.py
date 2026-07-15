from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

# 프로젝트 폴더에 local.db 파일이 생성됩니다
SQLALCHEMY_DATABASE_URL = "sqlite:///./local.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},  # SQLite + FastAPI 전용 옵션
)


# SQLite는 기본적으로 외래키(FK) 제약을 강제하지 않습니다.
# 연결할 때마다 PRAGMA로 켜줘야 명세의 CASCADE, SET NULL이 실제로 동작합니다.
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# 요청마다 DB 세션을 열고, 끝나면 닫아주는 함수 (Phase 3에서 사용)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()