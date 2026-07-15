from fastapi import FastAPI

app = FastAPI(title="LocalHub API")


@app.get("/")
def health_check():
    return {"success": True, "message": "LocalHub API 서버가 살아있습니다."}