FROM python:3.11-slim-bullseye

WORKDIR /app

# Install security updates and dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Tool Dependencies
# duckduckgo-search for web search
# beautifulsoup4 for scraping
# requests for http
RUN pip install --no-cache-dir \
    duckduckgo-search \
    beautifulsoup4 \
    requests \
    lxml

# Create a non-root user for security
RUN useradd -m -u 1000 agent
USER agent

COPY --chown=agent:agent . .

# Default entrypoint (overridden by MCP server for specific scripts)
CMD ["python3"]
