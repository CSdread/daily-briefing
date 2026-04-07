FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
# Run as the host Mac user's UID so the hostPath-mounted chat.db is readable.
# On most Macs the primary user is uid 501. Override with --build-arg if different.
ARG HOST_UID=501
RUN useradd -u ${HOST_UID} -m appuser 2>/dev/null || true
USER ${HOST_UID}
CMD ["python", "server.py"]
