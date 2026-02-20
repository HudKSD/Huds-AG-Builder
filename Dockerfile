FROM python:3.12-slim
WORKDIR /app
RUN useradd -m appuser
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend /app/backend
ENV PYTHONPATH=/app/backend
ENV APP_PORT=8800
USER appuser
EXPOSE 8800
CMD ["sh", "-c", "hypercorn app.main:app -b 0.0.0.0:${APP_PORT:-8800}"]
