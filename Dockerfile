FROM python:3.11-slim

# System dependencies for lxml, Pillow, EasyOCR, and pytesseract
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download EasyOCR English model at build time so first run is fast
RUN python -c "import easyocr; easyocr.Reader(['en'], gpu=False, verbose=False)"

COPY scraper.py manual_ingest.py .

# Input files and output sites folder are mounted at runtime (see docker-compose.yml)
CMD ["python", "scraper.py"]
