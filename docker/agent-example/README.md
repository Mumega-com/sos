# SOS Docker Agent Template

## Quick Start

1. Build:
   docker build -f docker/Dockerfile.agent -t my-agent .

2. Run:
   docker run -e SOS_TOKEN=sk-... -e SOS_AGENT_NAME=my-agent my-agent

3. With LangGraph:
   docker build -f docker/Dockerfile.agent --build-arg FRAMEWORK=langgraph -t lg-agent .

4. With CrewAI:
   docker build -f docker/Dockerfile.agent --build-arg FRAMEWORK=crewai -t crew-agent .

## Environment Variables

- SOS_TOKEN: Bus authentication token (required)
- SOS_AGENT_NAME: Agent name on the bus (default: my-agent)
- SOS_BUS_URL: Bus bridge URL (default: http://host.docker.internal:6380)
- SOS_SKILLS: Comma-separated skills (default: echo)
