# Либерея — образ Docker
#   docker run -d -p 5555:5555 -v ./data:/data -v /путь/к/книгам:/books:ro ghcr.io/icdev-ru/liberea
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LIBEREA_DATA=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY fonts ./fonts
COPY manage.py config.example.toml LICENSE ./

# база, кеш, настройки и модели перевода — в томе /data; книги монтируются в /books
VOLUME ["/data"]
EXPOSE 5555
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5555/opds', timeout=4)" || exit 1

CMD ["python", "manage.py", "serve"]
