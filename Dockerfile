FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

COPY requirements.txt .
# Install CPU-only PyTorch first so sentence-transformers doesn't pull the CUDA build
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir supervisor

COPY . .

# Ensure the data directory exists for SQLite
RUN mkdir -p /app/data

# Default port — cloud platforms (Render, Railway) override this via $PORT
ENV PORT=8000

EXPOSE 8000

# Default entrypoint for cloud deploys (all three processes in one container).
# Docker Compose overrides this per-service via the 'command:' key.
CMD ["supervisord", "-c", "/app/supervisord.conf"]
