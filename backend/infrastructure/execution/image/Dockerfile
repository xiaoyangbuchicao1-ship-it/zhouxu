FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer libreoffice-calc fonts-noto-cjk fonts-liberation poppler-utils && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir python-docx==1.2.0 openpyxl==3.1.5 lxml==6.0.2 typing_extensions==4.15.0 et_xmlfile==2.0.0 pdf2image==1.17.0 pillow==11.3.0
COPY zhouxu_office.py /usr/local/lib/python3.12/site-packages/zhouxu_office.py
USER 65534:65534
WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1 HOME=/workspace
CMD ["sleep", "3600"]
