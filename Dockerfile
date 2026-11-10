FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl iproute2 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app.py /app/app.py
EXPOSE 8099
CMD ["python3","-u","/app/app.py"]
