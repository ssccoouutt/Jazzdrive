# Use an official Python base image
FROM python:3.11-slim

# Install system dependencies and Google Chrome
RUN apt-get update && \
    apt-get install -y wget gnupg2 curl ffmpeg libc6 libglib2.0-0 libnss3 libgconf-2-4 libfontconfig1 libxss1 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 libxdamage1 libxext6 libxfixes3 libxi6 libxtst6 libappindicator1 libdbus-glib-1-2 libatk1.0-0 libatk-bridge2.0-0 libdrm2 libexpat1 libgtk-3-0 chromium-driver && \
    # Install Google Chrome (stable)
    wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && \
    apt-get install -y google-chrome-stable && \
    # Clean cache and temp files
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Create and set the working directory
WORKDIR /app

# Copy requirements.txt and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your bot code
COPY Jazzdrive.py .

# Set environment variables (disable pycache, etc.)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Entrypoint for container
CMD ["python", "Jazzdrive.py"]
