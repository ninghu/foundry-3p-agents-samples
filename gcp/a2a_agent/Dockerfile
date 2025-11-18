FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Install system dependencies required for building some Python packages
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . ./a2a_agent

# Ensure the package is importable when running as a module
ENV PYTHONPATH=/srv

# Default Cloud Run port
ENV PORT=8080

EXPOSE 8080

CMD ["python", "-m", "a2a_agent"]
