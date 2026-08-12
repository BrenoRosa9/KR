FROM python:3.12-slim-bookworm

# OCR (ocrmypdf/tesseract), qpdf para pikepdf e as bibliotecas do Pango/Cairo
# que o WeasyPrint exige para gerar o laudo em PDF.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-por \
        ghostscript \
        qpdf \
        unpaper \
        pngquant \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libffi8 \
        fonts-dejavu-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# As dependências mudam muito menos que o código: instalá-las antes aproveita
# o cache de camada nas atualizações do dia a dia.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install ".[server,ocr,report]"

# Sem root: se um PDF malformado explorar uma das bibliotecas de parsing, o
# estrago fica limitado ao que este usuário alcança.
RUN useradd --system --uid 10001 --home /srv kr \
    && mkdir -p /data && chown -R kr:kr /data /srv
USER kr

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
