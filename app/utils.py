import uuid
import bcrypt
from datetime import datetime, timezone, timedelta

# 한국 시간대 (+09:00)
KST = timezone(timedelta(hours=9))


def new_uuid() -> str:
    """8da8c240-c373-... 같은 UUID 문자열 생성"""
    return str(uuid.uuid4())


def now_iso() -> str:
    """현재 시각을 ISO 8601 형식으로 (예: 2026-07-15T13:00:00+09:00)"""
    return datetime.now(KST).replace(microsecond=0).isoformat()


def hash_password(pwd: str) -> str:
    """비밀번호를 해시로 변환해서 저장용 문자열로 반환"""
    return bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(pwd: str, hashed: str) -> bool:
    """입력한 비밀번호가 저장된 해시와 일치하는지 확인 (True/False)"""
    return bcrypt.checkpw(pwd.encode("utf-8"), hashed.encode("utf-8"))