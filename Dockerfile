FROM python:3.12-slim

WORKDIR /srv/workflow-ai

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 5003

CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "5003"]
