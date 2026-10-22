FROM python:3.11-slim

ARG INSTALL_ML=true
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/models

WORKDIR /app/backend
COPY pyproject.toml requirements.lock ./
# CPU-only torch keeps the image small; ML extras are optional (INSTALL_ML=false -> rules-only classifier)
RUN pip install --upgrade pip \
 && if [ "$INSTALL_ML" = "true" ]; then pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu; fi \
 && grep -v -E '^(torch|okc-backend)' requirements.lock > /tmp/req.txt \
 && if [ "$INSTALL_ML" != "true" ]; then grep -v -E '^(sentence-transformers|transformers|tokenizers|safetensors|huggingface-hub|nvidia)' /tmp/req.txt > /tmp/req2.txt && mv /tmp/req2.txt /tmp/req.txt; fi \
 && pip install -r /tmp/req.txt
COPY . .
RUN pip install --no-deps -e . && useradd -m okc && mkdir -p /models && chown okc /models
USER okc
EXPOSE 8000
# migrate, then serve; ingestion is triggered from the UI or `python -m app.cli ingest`
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
