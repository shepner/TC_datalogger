FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Build argument for cache busting (change this value to force rebuild of templates/static)
# Usage: docker build --build-arg CACHE_BUST=$(date +%s) ...
ARG CACHE_BUST=1

# Install system dependencies including docker CLI
# Aggressively clean up to minimize image size
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        docker.io \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
           /var/cache/apt/archives/* \
           /var/cache/apt/archives/partial/* \
           /var/cache/debconf/* \
           /tmp/* \
           /var/tmp/* \
    && find /usr/share -name "*.md" -delete \
    && find /usr/share -name "*.txt" -type f ! -name "copyright" -delete \
    && find /usr/share/doc -type f ! -name "copyright" -delete \
    && find /usr/share/man -type f -delete

# Copy requirements and install Python dependencies
# Clean pip cache and temporary files aggressively
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip cache purge && \
    rm -rf /root/.cache/pip/* \
           /tmp/* \
           /var/tmp/*

# Cache busting step - this will invalidate cache if CACHE_BUST changes
RUN echo "Cache bust: ${CACHE_BUST}" > /tmp/cache_bust.txt

# Copy application code
COPY src/ /app/src/
COPY templates/ /app/templates/
COPY static/ /app/static/

# Create logs directory and final cleanup
RUN mkdir -p /app/logs && \
    rm -rf /tmp/* /var/tmp/* && \
    find /app -type f -name "*.pyc" -delete && \
    find /app -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Expose dashboard port
EXPOSE 8080

# Run the Flask application
CMD ["python", "-m", "src.app"]

