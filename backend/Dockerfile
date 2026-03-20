# backend/Dockerfile
FROM python:3.13-slim

# Install uv for fast dependency installation
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy dependency manifests first for layer caching
COPY pyproject.toml uv.lock ./

# Install all production dependencies into system Python (no venv needed in container)
RUN uv pip install --system --no-cache .

# Copy application source
COPY . .

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
