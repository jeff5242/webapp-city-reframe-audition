FROM python:3.11-slim

WORKDIR /app

# System deps for Pillow (pdfplumber), OpenCV/PaddleOCR (libgl1, libglib2.0-0)
# and PaddlePaddle's OpenMP runtime (libgomp1 — required or import fails at runtime).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev \
    zlib1g-dev \
    nginx \
    libxcb1 \
    libglib2.0-0 \
    libgl1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer cache).
# build-essential is installed only for this layer (some deps, e.g. docling's
# stringzilla, build from source when no wheel exists for the target arch) and
# purged afterwards so it does not bloat the final image.
COPY pyproject.toml setup.py ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir \
        "numpy<2" \
        "fastapi>=0.115.0" \
        "uvicorn[standard]>=0.30.0" \
        "pdfplumber>=0.11.0" \
        "pyyaml>=6.0" \
        "jinja2>=3.1.0" \
        "python-multipart>=0.0.9" \
        "anthropic>=0.40.0" \
        "pymupdf>=1.23.0" \
        "boto3>=1.34.0" \
        "python-dotenv>=1.0.0" \
        "paddlepaddle>=2.6.0,<3.0.0" \
        "paddleocr>=2.7.0,<3.0.0" \
        "scikit-image>=0.21.0" \
        "pillow>=10.0.0" \
        "docling>=2.0.0" \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Pre-download PaddleOCR models so container startup is fast (no internet needed at runtime).
# Pinned to 2.x: the app uses the 2.x API (use_angle_cls/use_gpu/show_log, PPStructure).
RUN python3 -c "from paddleocr import PaddleOCR; PaddleOCR(use_angle_cls=True, lang='chinese_cht', use_gpu=False, show_log=False)" || true

# Copy source (after deps so source changes don't bust dep cache)
COPY . .

# nginx: replace default site, disable default site symlink if present
RUN cp nginx.conf /etc/nginx/sites-available/default \
    && chmod +x start.sh \
    && nginx -t

EXPOSE 8080
CMD ["/bin/bash", "start.sh"]
