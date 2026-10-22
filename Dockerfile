FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# JS-рантайм нужен yt-dlp для YouTube (решение JS-челленджей)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot

ENV PYTHONUNBUFFERED=1 DATA_DIR=/app/data
VOLUME /app/data
CMD ["python", "-m", "bot"]
