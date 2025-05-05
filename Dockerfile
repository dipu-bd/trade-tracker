FROM python:3.10-alpine

WORKDIR /app

COPY ./requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ./marketbot ./marketbot
COPY /.env ./.env

EXPOSE 8000
CMD ["python", "-m", "marketbot"]
