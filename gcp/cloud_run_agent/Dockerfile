FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential curl git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . ./cloud_run_agent

ENV PYTHONPATH=/srv
ENV PORT=8080

EXPOSE 8080

CMD ["uvicorn", "cloud_run_agent.main:app", "--host", "0.0.0.0", "--port", "8080"]
