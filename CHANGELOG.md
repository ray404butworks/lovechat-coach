# Changelog

All notable changes to OpenDate are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-11-14

### Added

- One-stop desktop home for incoming messages, the user's own reply intent, editable suggestions, explanations and copying. Detailed settings remain in a separate window.
- Controls for local automatic capture of visible incoming text from the selected Windows WeChat conversation, with title checks, explicit window selection, draft preservation and per-message review.

### Fixed

- Prevent long multiline profile and goal summaries from displacing input fields; keep action buttons and consent controls reachable at high DPI with scrollable content.
- Show generation failures and pause reasons directly on the home page, with a direct analysis action and a distinct save-and-generate control.
- Preserve the existing official ChatGPT HTTP transport configuration when running an otherwise isolated Codex CLI process, fixing repeated connection timeouts without raising the timeout.
- Clarify exact evidence quote formatting so speaker labels do not cause otherwise valid generated text to fail source validation.

### Validation

- 388 offline tests passed on Windows/Python 3.13; one native-display check was skipped. A synthetic conversation completed real Codex generation, exact-quote validation and independent review.
- Verified WeChat window restoration and stopping on a mismatched conversation. Correct-conversation body OCR remains unverified on the actual client; only visible light-theme text bubbles are supported.

## [0.3.0] - 2026-11-07

### Changed

- Rebuild the WeChat desktop coaching workflow around goutoujunshi's emotion, evidence, decision, one-action composition and feedback architecture.
- Add reply, analysis, expression review and one-turn rehearsal modes; replace the ten-skill desktop selector with scene routing and a current-question editor.
- Package 43 pinned MIT references with provenance and hash verification. Load only three relevant documents per request.
- Separate sendable text from internal coaching, validate evidence quotes and source IDs, and independently review the complete plan.
- Add bounded, consent-gated relationship memory with five scopes, provenance, upsert, pause, revoke, per-contact undo and hard deletion. Model candidates require explicit review before saving.
- Preserve v0.2 private settings, imported history, voice, identity mapping and the stage/invitation fixes. The legacy OpenDate CLI remains available.

### Fixed

- Separate friendly tone and banter from explicit romantic signals in WeChat automatic stage and skill selection.
- Replay visible invitation messages with acceptance, refusal, deferral, rescheduling, cancellation and expiry; old proposals and generated drafts no longer imply a current meeting discussion.
- Include invitation evidence, tone and judgment scope in strategy reports and all model workflow steps.

## [0.1.0] - 2026-06-07

Initial public release.

### Added

- **Multi-provider LLM router** built on `litellm` covering 19 provider routes —
  10 Western (OpenAI, Anthropic, Google Gemini, xAI Grok, Groq, Together,
  Mistral, Cohere, AWS Bedrock, Azure OpenAI) and 9 Chinese (DeepSeek, Alibaba
  Qwen, Zhipu GLM, Moonshot Kimi, Baidu ERNIE, 01.AI Yi, MiniMax, Tencent
  Hunyuan, ByteDance Doubao). Adding a provider is a single registry entry.
- Router resilience: timeouts, exponential-backoff retries, an ordered fallback
  model chain, a structured-JSON helper with safe parsing, and token-usage
  accounting. Deterministic offline stub LLM for demos and tests.
- **Connectors** behind a swappable `MatchSource` interface: a `tinder`
  connector (`httpx`, unofficial endpoints) with retry/backoff and pagination,
  and a `mock` connector for fully offline runs.
- **14 dating skills** in the agentskills.io `SKILL.md` format: profile-screening,
  opener, approaching, flirting, banter, rapport-building, storytelling,
  proposing-a-date, number-exchange, re-engagement, conversation-recovery,
  relationship-intent-matching, persona-style-transfer, and consent-and-safety.
- **Skills engine** with stage- and signal-aware, explainable skill selection
  and always-active modifiers.
- **Personality engine** that learns the user's voice from social posts and past
  chats (tone, vocabulary, emoji/punctuation rate, length/cadence, humor,
  go-to openers), produces a voice card, and powers style transfer. Degrades
  gracefully to heuristics with no LLM.
- **Async orchestrator** implementing the Sync to Screen to Decide to Generate to
  Voice to Guard to Act loop, with conversation memory + a stage machine,
  per-match error isolation, match prioritization, pacing/cooldowns, a daily
  action cap, a no-double-text rule, a dry-run plan, and structured decision
  logging.
- **Safety guard** as a blocking gate before every send: refuses deception,
  coercion/pressure, and explicit content (unless explicitly allowed), backs off
  on disinterest/discomfort, and hard-blocks possible-minor signals.
- **Message-quality critic** that scores drafts for genericness, cringe,
  repetition, and energy/length mismatch and regenerates weak drafts once.
- **Typer CLI** (`opendate`): `init`, `providers`, `skills`, `persona build/show`,
  `screen`, and `run`, plus a global `--mock` flag for keyless offline runs.
- Configuration via `config.yaml` + `.env`, with secret redaction in logs.
- Fully offline `pytest` suite and example posts/chats for persona building.

[Unreleased]: https://github.com/ray404butworks/lovechat-coach/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/ray404butworks/lovechat-coach/releases/tag/v0.3.1
[0.1.0]: https://github.com/sumitaich1998/OpenDate/releases/tag/v0.1.0
