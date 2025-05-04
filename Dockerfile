FROM python:3.10-alpine

WORKDIR /app

COPY ./requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ./marketbot/gold.py ./gold.py
COPY /.env ./.env

CMD ["python", "gold.py"]
