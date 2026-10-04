FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend
WORKDIR /app
COPY backend/requirements.lock.txt backend/requirements.lock.txt
RUN pip install --no-cache-dir -r backend/requirements.lock.txt \
    && groupadd --gid 10001 pedago \
    && useradd --uid 10001 --gid pedago --no-create-home pedago \
    && mkdir -p /data/documents && chown -R pedago:pedago /data
COPY backend/ backend/
COPY scripts/ scripts/
COPY compose.yaml compose.yaml
COPY --from=frontend /build/frontend/dist frontend/dist
USER pedago
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
