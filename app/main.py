from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import Base, engine
from app import models  # 이 import가 있어야 create_all이 4개 테이블을 인식합니다
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.responses import (
    success_response,
    http_exception_handler,
    validation_exception_handler,
)
from app.routers import posts, places
from seed import seed_all

# 서버 시작 시 테이블이 없으면 자동 생성 (있으면 건너뜀)
Base.metadata.create_all(bind=engine)
seed_all()

app = FastAPI(title="LocalHub API")

# ── CORS 설정 ─────────────────────────────────────
# .env의 FRONTEND_ORIGINS를 쉼표로 잘라서 리스트로 만든다
origins = [o.strip() for o in settings.frontend_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,       # 이 주소들에서 오는 요청만 허용
    allow_credentials=True,      # 쿠키(clientId) 주고받기 허용
    allow_methods=["*"],         # GET, POST, PATCH, DELETE 전부 허용
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────

# 실패 응답을 명세 형식으로 바꿔주는 핸들러 등록
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

app.include_router(posts.router)
app.include_router(places.router)

@app.get("/")
def health_check():
    return success_response(message="LocalHub API 서버가 살아있습니다.")