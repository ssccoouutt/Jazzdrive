FROM python:3.11-slim

# Install system dependencies and Google Chrome
RUN apt-get update && \
    apt-get install -y \
        wget \
        gnupg2 \
        curl \
        ffmpeg \
        libc6 \
        libglib2.0-0 \
        libnss3 \
        libfontconfig1 \
        libxss1 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxcursor1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxi6 \
        libxtst6 \
        libdbus-glib-1-2 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libdrm2 \
        libexpat1 \
        libgtk-3-0 \
        chromium-driver \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY Jazzdrive.py .

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

CMD ["python", "Jazzdrive.py"]
