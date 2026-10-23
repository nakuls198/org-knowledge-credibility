FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models MODEL_CACHE_DIR=/models

WORKDIR /app/backend
COPY pyproject.toml requirements.lock ./
RUN pip install --upgrade pip \
 && pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu \
 && grep -v -E '^(torch|okc-backend)' requirements.lock > /tmp/req.txt \
 && pip install -r /tmp/req.txt

# Download both models now so the container never needs internet at run time.
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', cache_folder='/models'); \
CrossEncoder('cross-encoder/nli-deberta-v3-base', cache_dir='/models')"

COPY . .
RUN pip install --no-deps -e . && chmod +x docker-entrypoint.sh \
 && useradd -m okc && chown -R okc /models
USER okc
EXPOSE 8000
CMD ["./docker-entrypoint.sh"]
