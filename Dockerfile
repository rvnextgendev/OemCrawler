FROM python:3.12-slim
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && python -m playwright install --with-deps chromium
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "app.gateway:app", "--host", "0.0.0.0", "--port", "8000"]
