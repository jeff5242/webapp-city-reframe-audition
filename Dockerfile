FROM python:3.11-slim

WORKDIR /app

# Install system deps required by Pillow (used by pdfplumber)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml setup.py ./
RUN pip install --no-cache-dir -e .

COPY . .

EXPOSE 8080
CMD ["uvicorn", "auditor.main:app", "--host", "0.0.0.0", "--port", "8080"]
