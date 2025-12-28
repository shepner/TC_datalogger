FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ /app/src/
COPY config/ /app/config/
COPY docker/entrypoint.sh /app/docker/entrypoint.sh

# Make entrypoint executable
RUN chmod +x /app/docker/entrypoint.sh

# Create logs directory
RUN mkdir -p /app/logs

# Set entrypoint
ENTRYPOINT ["/app/docker/entrypoint.sh"]
