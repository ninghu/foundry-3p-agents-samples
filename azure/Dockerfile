FROM mcr.microsoft.com/devcontainers/python:1-3.12-bullseye

WORKDIR /app

USER root

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=80

COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 80

CMD ["uvicorn", "agent_framework_container_app:app", "--host", "0.0.0.0", "--port", "80"]
