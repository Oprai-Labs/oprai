"""Entry point: python -m app"""
import uvicorn
from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.BIND_HOST,
        port=settings.PORT,
        reload=True,
    )
