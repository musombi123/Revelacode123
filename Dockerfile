FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY legend ./legend
COPY library ./library
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "legend.api:app", "--host", "0.0.0.0", "--port", "8000"]
