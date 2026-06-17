FROM node:lts AS base

ARG CONTAINER_GID=2523
RUN groupadd -g ${CONTAINER_GID} Wywy-Website && \
    useradd -m -g Wywy-Website wywy

WORKDIR /app

COPY --chown=wywy:Wywy-Website apps/astro/package.json apps/astro/package-lock.json* ./
RUN chown -R wywy:Wywy-Website /app
USER wywy
RUN npm install

FROM base AS dev
CMD ["npx", "astro", "preview", "--host"]


FROM base AS prod
COPY --chown=wywy:Wywy-Website apps/astro/ ./
RUN npm run build

CMD ["npx", "astro", "preview", "--host"]


FROM base AS test
# Pre-install Playwright Chromium browser and system deps so they
# don't need to download at test time (~300 MB savings per run).
# Playwright 1.48+ ships the headless shell as a separate binary that
# must be installed explicitly alongside chromium.
ENV PLAYWRIGHT_BROWSERS_PATH=/home/wywy/.cache/ms-playwright
USER root
RUN npx playwright install --with-deps chromium chromium-headless-shell && \
    chown -R wywy:Wywy-Website /home/wywy/.cache
USER wywy
CMD ["bash", "scripts/test.sh"]
