FROM python:3.10-alpine

WORKDIR /app

COPY ./requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ./marketbot ./marketbot
COPY /.env ./.env

# The SQLite database lives on a mounted volume so the portfolio survives
# container replacement.
VOLUME /data
ENV MARKETBOT_DB_URL=sqlite:////data/marketbot.db

EXPOSE 8000
CMD ["python", "-m", "marketbot"]
