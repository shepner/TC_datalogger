FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install system dependencies including docker CLI
RUN apt-get update && apt-get install -y \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ /app/src/
COPY templates/ /app/templates/
COPY static/ /app/static/

# Create logs directory (for consistency, though dashboard doesn't write logs)
RUN mkdir -p /app/logs

# Expose dashboard port
EXPOSE 8080

# Run the Flask application
CMD ["python", "-m", "src.app"]

