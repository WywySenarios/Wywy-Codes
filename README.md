# Wywy-Codes

## API Key Secrets

API keys for LLM providers are read from **Docker secret files** at runtime, not from environment variables or `.env`. Each provider key is read from a file in `API_KEY_SECRETS_DIR` (default `/run/secrets/`).

| Provider | Env var passed to agent | Secret file |
|----------|------------------------|-------------|
| OpenCode | `OPENCODE_API_KEY` | `/run/secrets/opencode-api-key` |
| DeepSeek | `DEEPSEEK_API_KEY` | `/run/secrets/deepseek-api-key` |
| OpenAI | `OPENAI_API_KEY` | `/run/secrets/openai-api-key` |
| Anthropic | `ANTHROPIC_API_KEY` | `/run/secrets/anthropic-api-key` |

The file name is derived by **lowercasing** the key name and replacing underscores with hyphens. For example, `OPENCODE_API_KEY` maps to `opencode-api-key`.

### Local development

```bash
mkdir -p /run/secrets
echo "sk-your-opencode-key" > /run/secrets/opencode-api-key
echo "sk-your-deepseek-key" > /run/secrets/deepseek-api-key
echo "sk-your-openai-key"   > /run/secrets/openai-api-key
echo "sk-your-anthropic-key" > /run/secrets/anthropic-api-key
```

If a secret file does not exist or cannot be read, the key defaults to an empty string (`""`). The agent server still starts, but no LLM provider will be available.

### Docker Compose / Swarm

Mount secret files using the `secrets` top-level key:

```yaml
secrets:
  opencode-api-key:
    file: ./secrets/opencode-api-key
  deepseek-api-key:
    file: ./secrets/deepseek-api-key
  openai-api-key:
    file: ./secrets/openai-api-key
  anthropic-api-key:
    file: ./secrets/anthropic-api-key

services:
  django:
    secrets:
      - opencode-api-key
      - deepseek-api-key
      - openai-api-key
      - anthropic-api-key
```

### Overriding the secrets directory

Set the `API_KEY_SECRETS_DIR` environment variable to change the directory searched for secret files (defaults to `/run/secrets`).

## Logging

Logging in this repo splits into two independent systems:

| Log | Function | Destination | Visible in frontend |
|-----|----------|-------------|-------------------|
| **Orchestrator** | `_write_orchestrator_log()` | `orchestrator.log` per pipeline | Yes — `LogViewer` reads this |
| **Django** | `logger.*` | stdout + `django.log` | No — system diagnostics only |

**Rule:** All pipeline lifecycle events (start, stage transitions, end, errors, teardown) go to the **orchestrator log**. Use `logger.*` only for infrastructure diagnostics (network, containers, token config).
