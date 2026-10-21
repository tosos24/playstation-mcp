FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Web mode. NPSSO and MCP_SECRET come from the host's environment variables, never the image.
ENV MODE=http PYTHONUNBUFFERED=1
CMD ["python", "server.py"]
