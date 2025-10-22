FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app

# All environment variables in one layer
ENV PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1 \
    AWS_REGION=us-west-2 \
    AWS_DEFAULT_REGION=us-west-2 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt requirements.txt

# Install system dependencies needed for git-based pip installs
RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies using pip
RUN pip install -r requirements.txt

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080
EXPOSE 8000

# Copy entire project (respecting .dockerignore)
COPY . .

CMD ["python", "-m", "agentcore_langgraph_agent"]
