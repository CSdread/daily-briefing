FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run_agent.py .

# Non-root user
RUN useradd -u 1000 -m agent
USER agent

CMD ["python", "run_agent.py"]
