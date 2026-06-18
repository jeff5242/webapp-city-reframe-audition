FROM python:3.11-slim

WORKDIR /app

# System deps for Pillow (used by pdfplumber)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer cache)
COPY pyproject.toml setup.py ./
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.30.0" \
    "pdfplumber>=0.11.0" \
    "pyyaml>=6.0" \
    "jinja2>=3.1.0" \
    "python-multipart>=0.0.9" \
    "anthropic>=0.40.0" \
    "pymupdf>=1.23.0"

# Copy source (after deps so source changes don't bust dep cache)
COPY . .

EXPOSE 8080
CMD ["uvicorn", "auditor.main:app", "--host", "0.0.0.0", "--port", "8080"]
