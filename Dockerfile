FROM python:3.12-slim

WORKDIR /srv/workflow-ai

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 5003

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5003"]
