FROM node:lts AS astro-build

WORKDIR /astro
COPY apps/astro/package.json apps/astro/package-lock.json* ./
RUN npm ci
COPY apps/astro/ ./
ARG PUBLIC_AGENTIC_API_HOST
ARG PUBLIC_AGENTIC_API_PORT
ENV PUBLIC_AGENTIC_API_HOST=${PUBLIC_AGENTIC_API_HOST:-lolipop}
ENV PUBLIC_AGENTIC_API_PORT=${PUBLIC_AGENTIC_API_PORT:-2524}
RUN npm run build

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    jq \
    nodejs \
    npm \
    clang-format \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir ruff

RUN npm install -g prettier

RUN pip install --no-cache-dir \
    django>=5.0 \
    djangorestframework \
    django-cors-headers \
    docker>=7.0 \
    gunicorn \
    pyyaml \
    whitenoise>=6.0

ARG CONTAINER_GID=2523
ARG DOCKER_GID=989
RUN groupadd -g ${CONTAINER_GID} Wywy-Website && \
    groupadd -g ${DOCKER_GID} docker_host && \
    useradd -m -g Wywy-Website -G docker_host wywy

RUN mkdir -p /var/log/Wywy-Website/agentic && \
    mkdir -p /var/lib/Wywy-Website/orchestrator && \
    mkdir -p /var/workspace/Wywy-Website && \
    chown -R wywy:Wywy-Website /var/log/Wywy-Website /var/lib/Wywy-Website /var/workspace/Wywy-Website

WORKDIR /app

COPY apps/django/apps/         /app/apps/
COPY apps/django/config/       /app/config/
COPY apps/django/manage.py     /app/

COPY --from=astro-build /astro/dist /app/astro-dist
ENV ASTRO_DIST=/app/astro-dist

USER wywy

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --threads 2"]
