FROM python:3.12-slim
WORKDIR /app
RUN useradd -m appuser
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend /app/backend
ENV PYTHONPATH=/app/backend
USER appuser
EXPOSE 8000
CMD ["hypercorn", "app.main:app", "-b", "0.0.0.0:8000"]
