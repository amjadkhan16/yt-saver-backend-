FROM python:3.11-slim

# ffmpeg = video+audio merge aur MP3 ke liye lazmi
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

# Render/HF: platform jo PORT de use wahi (Render default 10000 deta hai)
EXPOSE 10000
CMD sh -c "uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}"
