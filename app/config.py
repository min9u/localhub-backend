from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 쉼표로 구분된 허용 출처 목록 (CORS용)
    # 예: "http://localhost:5173,https://localhost.netlify.app"
    frontend_origins: str = "http://localhost:5173"

    class Config:
        env_file = ".env"


settings = Settings()