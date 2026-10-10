FROM python:3.12-slim
ENV APP_ENV=production HOST=0.0.0.0 PORT=3000 DATABASE_PATH=/app/data/couriercloud.sqlite PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 1000 --create-home courier
COPY --chown=courier:courier couriercloud ./couriercloud
COPY --chown=courier:courier public ./public
COPY --chown=courier:courier gunicorn.conf.py ./
RUN mkdir -p /app/data && chown courier:courier /app/data
USER courier
EXPOSE 3000
VOLUME ["/app/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/health', timeout=3)"
CMD ["gunicorn", "--config", "gunicorn.conf.py", "couriercloud.wsgi:application"]
