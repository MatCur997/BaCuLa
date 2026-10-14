import uvicorn

from .config import Settings

if __name__ == "__main__":
    settings = Settings.from_env()
    uvicorn.run("app.main:get_app", factory=True, host=settings.host, port=settings.port)
