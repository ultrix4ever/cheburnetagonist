FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home vpnmax
COPY --chown=10001:10001 vpnmax ./vpnmax
COPY --chown=10001:10001 certs ./certs
USER 10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/health', timeout=3)"
CMD ["uvicorn", "vpnmax.app:from_env", "--factory", "--host", "0.0.0.0", "--port", "8088", "--workers", "1", "--no-access-log"]
