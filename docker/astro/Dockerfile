FROM node:lts

ARG CONTAINER_GID=2523
RUN groupadd -g ${CONTAINER_GID} Wywy-Website && \
    useradd -m -g Wywy-Website wywy

WORKDIR /app

COPY --chown=wywy:Wywy-Website apps/astro/package.json apps/astro/package-lock.json* ./
RUN chown -R wywy:Wywy-Website /app
USER wywy
RUN npm install

COPY --chown=wywy:Wywy-Website apps/astro/ ./
RUN npm run build

EXPOSE 3000
CMD ["node", "dist/server/entry.mjs"]
