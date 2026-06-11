/// <reference path="../.astro/types.d.ts" />

interface ImportMetaEnv {
  readonly PUBLIC_AGENTIC_API_HOST: string;
  readonly PUBLIC_AGENTIC_API_PORT: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
