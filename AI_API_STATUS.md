# AI API status

Audited 2026-11-06 against `https://api.omegatech.app` using short text-only prompts for general chat, reasoning, and coding. Image, video, audio, and other media endpoints were excluded.

## Active verified operatives

These routes returned usable text responses during the live smoke test and are exposed to users and administrators:

- `gpt-4o-mini` -> `/api/ai/Gpt-4-mini`
- `deepseek-cli` -> `/api/ai/Aicli`
- `chatgpt-v2` -> `/api/ai/Chatgpt-v2`
- `gemini-pro` -> `/api/ai/gemini-pro`, with `{ "text": "..." }`

## Removed from the active catalog

The following routes were removed from active plan pools, coding pools, fallback chains, and admin-selectable operatives after live failures:

- Claude, Claude Chat, Qwen/Agents, and all Agents model variants: HTTP 500 or unavailable request contract.
- Deep-ai model variants: HTTP 200 but no usable answer.
- Gemini Flash Lite, GPT OSS 120B, GPT-5 Nano, and related Deep-ai aliases: no usable answer.

The documented Agents page says it supports chat and coder sandbox requests, but the live endpoint returned HTTP 500 for both the `messages[]` chat contract and the documented coder models `qwen-coder-32b` and `deepseek-coder`. They remain excluded until the provider fixes the route.

## Candidates not activated

The following documented text-only candidates were tested but were not activated because they failed or returned an unusable response:

- Kimi: HTTP 500.
- Gemini 2.0 Flash: HTTP 500.
- Gemini Premium: HTTP 200 with an empty answer.
- DeepSeek alternate route: HTTP 200 with an empty answer.
- Qwen MV2: HTTP 200 but only a provider upgrade notice.
- LlamaCoder and Mistral: HTTP 400 with the tested text request contract.

## Official documentation

- [OmegaTech API home](https://api.omegatech.app/)
- [OmegaTech AI reference](https://api.omegatech.app/api-docs/Omega)
- [Agents documentation](https://api.omegatech.app/api-docs/Agents)
- [Gemini documentation](https://api.omegatech.app/api-docs/Gemini)
- [Gemini Pro documentation](https://api.omegatech.app/api-docs/gemini-pro)
- [GPT-4o Mini documentation](https://api.omegatech.app/api-docs/gpt-4o-mini)
- [Kimi documentation](https://api.omegatech.app/api-docs/kimi)
- [LlamaCoder documentation](https://api.omegatech.app/api-docs/llamacoder)
- [Mistral documentation](https://api.omegatech.app/api-docs/Mistral)
