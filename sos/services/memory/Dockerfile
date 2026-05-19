FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y build-essential curl

# Install Python dependencies
COPY pyproject.toml .
RUN pip install .

# Copy Code
COPY sos/ sos/

# Environment
ENV PYTHONPATH=/app
ENV SOS_HOME=/app/data

# Command
CMD ["python", "-m", "sos.services.memory"]
