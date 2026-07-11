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
        "paddlepaddle>=3.0.0" \
        "paddleocr>=3.0.0" \
        "opencc-python-reimplemented>=0.1.7" \
        "scikit-image>=0.21.0" \
        "pillow>=10.0.0" \
        "docling>=2.0.0" \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# oneDNN 在 paddle-3.0 CPU 有 op bug（strides/ConvertPirAttribute）→ 全程關閉。
ENV FLAGS_use_mkldnn=0

# Pre-download PP-OCRv5 models so container startup is fast (no internet needed at runtime).
# paddleocr 3.x API：predict()、預設統一 rec（繁簡通用）；不傳 lang。
RUN python3 -c "from paddleocr import PaddleOCR; PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)" || true

# Copy source (after deps so source changes don't bust dep cache)
COPY . .

# nginx: replace default site, disable default site symlink if present
RUN cp nginx.conf /etc/nginx/sites-available/default \
    && chmod +x start.sh \
    && nginx -t

EXPOSE 8080
CMD ["/bin/bash", "start.sh"]
