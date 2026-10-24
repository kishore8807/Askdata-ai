"""Application entrypoint re-exporting FastAPI app from backend.main.

Allows running either:
    uvicorn main:app --reload
or:
    uvicorn backend.main:app --reload
"""
from backend.main import app

__all__ = ["app"]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
