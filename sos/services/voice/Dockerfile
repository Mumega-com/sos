FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y build-essential curl

# Install Python dependencies
COPY pyproject.toml .
RUN pip install . && pip install elevenlabs

# Copy Code
COPY sos/ sos/

# Environment
ENV PYTHONPATH=/app
ENV SOS_HOME=/app/data
ENV VOICE_SERVICE_PORT=6065

# Expose port
EXPOSE 6065

# Command
CMD ["python", "-m", "sos.services.voice.app"]
