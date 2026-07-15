from http import HTTPStatus
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


def success_response(data=None, message="요청에 성공했습니다."):
    """성공 응답을 명세 형식으로 감싸주는 헬퍼 함수"""
    return {
        "success": True,
        "message": message,
        "data": data,
    }


def _error_name(status_code: int) -> str:
    """상태 코드 → 영문 이름 (400 → 'Bad Request', 404 → 'Not Found')"""
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """raise HTTPException(...) 이 발생하면 명세의 실패 형식으로 변환"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "statusCode": exc.status_code,
            "message": exc.detail,
            "error": _error_name(exc.status_code),
        },
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """요청 데이터 검증 실패(422)도 같은 실패 형식으로 변환"""
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "statusCode": 422,
            "message": "입력값이 올바르지 않습니다.",
            "error": "Unprocessable Entity",
        },
    )