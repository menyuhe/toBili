FROM python:3.13-slim
WORKDIR /app
COPY cloud/requirements.txt /app/cloud/requirements.txt
RUN pip install --no-cache-dir -r cloud/requirements.txt
COPY cloud /app/cloud
COPY src/__init__.py src/cloud_protocol.py /app/src/
RUN mkdir -p /app/data && chown -R 10001:10001 /app
USER 10001:10001
EXPOSE 8787
CMD ["uvicorn", "cloud.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8787", "--proxy-headers", "--forwarded-allow-ips", "*"]
