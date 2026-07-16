from pydantic_settings import BaseSettings

'''
API 키, 프론트엔드 주소 같은 환경마다 달라지는 값을 관리
'''


class Settings(BaseSettings):
    # 쉼표로 구분된 허용 출처 목록 (CORS용)
    # 예: "http://localhost:5173,https://localhost.netlify.app"
    frontend_origins: str = "http://localhost:5173"

    # OpenAI API 키 — .env에 OPENAI_API_KEY=sk-... 로 등록 (Render에선 환경변수로)
    openai_api_key: str = ""

    class Config:
        env_file = ".env"


settings = Settings()