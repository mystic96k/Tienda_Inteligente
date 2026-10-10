FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY db ./db
COPY frontend ./frontend
RUN useradd -m app && mkdir -p /srv/models /data && chown -R app /srv /data
USER app
ENV MODEL_DIR=/srv/models DATA_DIR=/data PORT=8000 FRONTEND_DIR=/srv/frontend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
