FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock && useradd --uid 10001 --create-home app
COPY --chown=app:app majlis ./majlis
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app web ./web
USER app
CMD ["python", "-m", "majlis.cli", "api"]
