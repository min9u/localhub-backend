from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import Base, engine
from app import models  # 이 import가 있어야 create_all이 4개 테이블을 인식합니다
from app.responses import (
    success_response,
    http_exception_handler,
    validation_exception_handler,
)
from app.routers import posts

# 서버 시작 시 테이블이 없으면 자동 생성 (있으면 건너뜀)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="LocalHub API")

# 실패 응답을 명세 형식으로 바꿔주는 핸들러 등록
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)

app.include_router(posts.router)

@app.get("/")
def health_check():
    return success_response(message="LocalHub API 서버가 살아있습니다.")