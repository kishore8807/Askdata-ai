FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
COPY database ./database
RUN useradd --system --uid 10001 appuser
USER appuser
EXPOSE 8000 5000
# default: API.  The compose file overrides the command for the web front door.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
