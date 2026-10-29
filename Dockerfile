FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && \
    rm -rf /root/.cache/uv

COPY app ./app

RUN mkdir -p uploads

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "--frozen", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000"]
