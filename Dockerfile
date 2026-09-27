FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8080
CMD ["sh","-c","gunicorn -w 2 -b 0.0.0.0:$PORT --timeout 120 app:app"]
