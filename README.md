# Wywy-Codes

## Logging

Logging in this repo splits into two independent systems:

| Log | Function | Destination | Visible in frontend |
|-----|----------|-------------|-------------------|
| **Orchestrator** | `_write_orchestrator_log()` | `orchestrator.log` per pipeline | Yes — `LogViewer` reads this |
| **Django** | `logger.*` | stdout + `django.log` | No — system diagnostics only |

**Rule:** All pipeline lifecycle events (start, stage transitions, end, errors, teardown) go to the **orchestrator log**. Use `logger.*` only for infrastructure diagnostics (network, containers, token config).
