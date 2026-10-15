FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir \
    openpyxl==3.1.5 \
    pypdf==6.1.1 \
    python-docx==1.2.0 \
    reportlab==4.4.4

WORKDIR /workspace
