# AGENTS.md — Wywy-Codes

**Service name:** `agentic`

## Configuration

### Environment files

Env files are loaded in this order (later files override earlier):

| Priority | File | Purpose |
|----------|------|---------|
| 1 | `/etc/Wywy-Website-Control/config/.env` | Shared control config (domains, URLs, data dirs) |
| 2 | `/etc/Wywy-Website-Control/config/.env.network` | Network/host config (`ALLOWED_HOSTS`, etc.) |
| 3 | `/etc/Wywy-Website-Control/config/agentic/.env` | Agentic-specific (app defaults, pipeline, orchestrator, container GID) |

All three are loaded via `env_file:` in `docker-compose.base.yml`. Dev extra
files (`/etc/Wywy-Website-Control/config/.env.dev`, `agentic/.env.dev`)
append to the `env_file` list in `docker-compose.dev.yml`.

Mode-specific overrides (`DJANGO_DEBUG`, `DJANGO_SETTINGS_MODULE`) remain
in the compose override files.

External deployment config is loaded from `/etc/Wywy-Website-Control/config/agentic/`.

## Stack

- **django** — orchestrator backend (DRF, pipeline executor)
- **astro** — SSR frontend (Astro 5, React 19, Tailwind CSS 4)
- **agent** — ephemeral pipeline container (wywy/agent image)
