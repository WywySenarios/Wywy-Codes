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
