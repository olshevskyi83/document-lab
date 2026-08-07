FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    REMOTE_ROOT=/remote \
    DATA_ROOT=/app/data \
    LOGS_ROOT=/app/logs

RUN apt-get update && apt-get install -y --no-install-recommends \
    djvulibre-bin \
    ghostscript \
    pngquant \
    poppler-utils \
    qpdf \
    tesseract-ocr \
    tesseract-ocr-deu \
    tesseract-ocr-eng \
    tesseract-ocr-rus \
    tesseract-ocr-spa \
    tesseract-ocr-ukr \
    unpaper \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
COPY app ./app

RUN mkdir -p /app/data /app/logs /remote/Documents/Library \
    && useradd --system --uid 10001 --home /app documentlab \
    && chown -R documentlab:documentlab /app /remote

USER documentlab
EXPOSE 3012
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3012/health', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3012"]
