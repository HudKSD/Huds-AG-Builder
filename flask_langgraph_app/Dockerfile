FROM python:3.11-slim

WORKDIR /app
RUN useradd -m appuser
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
