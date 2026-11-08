FROM python:3.11-slim

# Prevent python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    CCTV_DATA=/app/data

# Install system libraries needed by OpenCV and networking
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application codebase
COPY app/ app/
COPY static/ static/
COPY templates/ templates/
COPY models/ models/
COPY run.py .
COPY entrypoint.sh .

RUN chmod +x /app/entrypoint.sh

EXPOSE 10000 8000

ENTRYPOINT ["/bin/bash", "/app/entrypoint.sh"]
