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

RUN npm install -g prettier pyright

RUN pip install --no-cache-dir \
    django>=5.0 \
    djangorestframework \
    django-cors-headers \
    docker>=7.0 \
    gunicorn \
    pyyaml \
    whitenoise>=6.0 \
    pytest \
    pytest-django \
    pytest-asyncio \
    httpx>=0.27

ARG CONTAINER_GID=2523
ARG CONTAINER_UID=25233
ARG DOCKER_GID=989
RUN groupadd -g ${CONTAINER_GID} Wywy-Website && \
    groupadd -g ${DOCKER_GID} docker_host && \
    useradd -m -u ${CONTAINER_UID} -g Wywy-Website -G docker_host wywy

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

RUN git config --global user.email "wywy@localhost" && \
    git config --global user.name "Wywy" && \
    git config --global init.defaultBranch master

EXPOSE 8000
# --preload must NOT be used: OrchestratorConfig.ready() uses fcntl.flock()
# to elect a single orchestrator worker, and --preload would run ready() in
# the master process before fork, causing all children to inherit the lock.
CMD ["sh", "-c", "umask 002 && python manage.py check_db_writable && python manage.py migrate && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-2} --threads 2"]
