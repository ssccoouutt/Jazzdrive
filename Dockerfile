FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    --no-install-recommends

# Install Google Chrome Stable (hardcoded version for stability)
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable=120.0.6099.109-1 \
    && rm -rf /var/lib/apt/lists/*

# Install a specific version of ChromeDriver known to be compatible with the Chrome version above.
# This version is compatible with Chrome 120.0.6099.109
RUN wget -q https://storage.googleapis.com/chrome-for-testing-public/120.0.6099.109/linux64/chromedriver-linux64.zip -O /tmp/chromedriver.zip \
    && unzip /tmp/chromedriver.zip -d /usr/local/bin/ \
    && mv /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver \
    && chmod +x /usr/local/bin/chromedriver \
    && rm /tmp/chromedriver.zip && rm -rf /usr/local/bin/chromedriver-linux64

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "Jazzdrive.py"]


