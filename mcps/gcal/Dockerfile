FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY authorize.py .

RUN useradd -u 1000 -m mcpuser
USER mcpuser

EXPOSE 3001

CMD ["python", "server.py"]
