# AGENTS.md — Wywy-Codes

**Service name:** `agentic`

## Critical property: atomicity and thread-safety

**EVERY** code path that reads or writes pipeline/stage state runs
concurrently across **N** Gunicorn worker processes (``--workers ${WEB_CONCURRENCY:-2}``,
see ``Dockerfile`` CMD).  Each worker has its own orchestrator daemon
thread and its own WSGI request handlers.  There is **no** inter-process
coordination beyond the database row lock (``select_for_update``) and a
per-process ``Queue`` for abort signals.

This means:

- Two orchestrator threads can call ``advance_pipeline()`` on the same
  pipeline within milliseconds of each other.
- A WSGI request handler (``api_respond``, ``api_abort``) can mutate
  pipeline state while an orchestrator thread in another worker is
  simultaneously advancing stages.
- The ``_teardown_completed`` set is **per-process**, so two workers
  can both attempt to tear down the same workspace.
- Any change that is not safe under concurrent execution will corrupt
  pipeline state or silently destroy another worker's data.

**Before adding any state mutation**, ask: *"What happens if two workers
do this at the same time?"*  If the answer is not "nothing bad", the
change must use ``select_for_update``, ``transaction.atomic()``, or
some other synchronisation primitive.

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

## Running tests

Run the full test suite (including integration tests against a real opencode server container) from any working directory:

```bash
/etc/Wywy-Website-Control/run.sh agentic test up
```

This uses the ``test`` target of the agentic service script, which merges the
base, dev, and test docker-compose files, loads the production env files (CORS
configuration, API keys, etc.), builds the dev Django image, and starts the
services.  The Django service runs ``pytest`` as its entrypoint and shuts down
automatically after completion.  The ``astro`` dev server also starts
concurrently (frontend tests are separate).

The test docker-compose file mounts the Docker socket so integration tests can
spawn real opencode server containers.  The environment variable
``OPENCODE_INTEGRATION_TEST=1`` is set so those tests run; they were previously
skipped by default.

> **Note:** To run only the backend tests without the Astro frontend service,
> invoke the agentic script directly:
> ```bash
> /etc/Wywy-Website-Control/scripts/run/agentic.sh run test --rm django
> ```

## Stack

- **django** — orchestrator backend (DRF, pipeline executor)
- **astro** — SSR frontend (Astro 5, React 19, Tailwind CSS 4)
- **agent** — ephemeral pipeline container (wywy/agent image)
