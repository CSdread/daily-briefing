FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run_agent.py mcp_client.py memory.py service_runner.py entrypoint.sh .
RUN chmod +x entrypoint.sh

# Non-root user
RUN useradd -u 1000 -m agent
USER agent

CMD ["./entrypoint.sh"]
