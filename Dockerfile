# ==============================================================================
# Stage 1: Build React 19 Frontend
# ==============================================================================
FROM node:22-alpine AS frontend-builder
WORKDIR /app/frontend

# Copy package manifests and install dependencies
COPY frontend/package*.json ./
RUN npm install

# Copy frontend source and build production distribution
COPY frontend/ ./
RUN npm run build

# ==============================================================================
# Stage 2: Python 3.12 Runtime Daemon
# ==============================================================================
FROM python:3.12-slim-bookworm AS runtime

# System runtime dependencies:
# - openssh-client: for SSH client/keygen utilities
# - rsync: for workspace incremental file synchronization
# - curl, ca-certificates: for healthchecks and secure outbound HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    rsync \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Runtime environment settings
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=1113 \
    PYTHONPATH=/app/backend

# Create directory hierarchy and mount targets
RUN mkdir -p /data /workspace /app/backend /app/frontend/dist

WORKDIR /app/backend

# Install Python dependencies first for optimal Docker layer caching
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source tree
COPY backend/ ./

# Copy compiled frontend assets from Stage 1
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Expose Web Console / API port (1113) and SSH multiplexer port pool (2220-2229)
EXPOSE 1113
EXPOSE 2220-2229

# Persistent storage mount points
VOLUME ["/data", "/workspace"]

# Healthcheck targeting the HTTP port 1113
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:1113/healthz || exit 1

# Start Uvicorn ASGI daemon on port 1113
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "1113"]
