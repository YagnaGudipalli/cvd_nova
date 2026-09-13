FROM python:3.12-slim

# Hugging Face Spaces run the container as uid 1000; everything the app writes
# (the SQLite ledger, the embedding model cache) must belong to that user.
RUN useradd --create-home --uid 1000 user

ENV HOME=/home/user \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    PORT=7860

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY docs/examples ./docs/examples

RUN mkdir -p /app/data /app/.cache/fastembed && chown -R user:user /app
USER user

# Bake the embedding model into the image. Downloaded at runtime instead, every
# cold start after the Space sleeps would pay for it inside the first research run.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

EXPOSE 7860
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT}"]
