# Wotan

Wotan is a self-hosted agent workbench for software development and
non-coding "assistant" work on **Windows 10/11 without admin rights**
(and Linux/macOS for development): a chat/agent UI with a built-in IDE
(editor, search, git, terminal), a Frankenstein-proof edit engine, a mandatory
verification harness, and first-class integration with a **corporate LLM
gateway** - configured entirely in YAML, without code changes.

Everything is bundled locally (Monaco + CodeMirror + xterm + fonts + icons):
no runtime downloads from CDNs. The only network calls are the configured
gateway endpoints, the identity token API, and (optionally) your web search
endpoint.

## Features

- **IDE**: gitignore-aware file tree, tabs with split view and modified
  markers, syntax highlighting (Monaco -> CodeMirror 6 -> textarea fallback),
  cross-file search (ripgrep when available, pure-Python fallback) with
  in-file replace, multi-terminal (ConPTY on Windows via pywinpty), side-by-side
  diffs, git panel, VS Code shortcuts, dark/light/follow-system themes.
- **Agent**: explore -> plan -> execute -> verify -> fix loop, always-visible
  status (timers, tokens, steps, live tool cards, stall detection, stop),
  streaming, interrupt, mid-run questions, approval cards, planning mode,
  parallel sub-agents in git worktrees, checkpoints with per-step undo.
- **Frankenstein-proof editing** (critical): structured JSON edits
  (`path` / `old_string` / `new_string` / `replace_all`), exactly-once match,
  atomic `multi_edit`, mandatory read-before-edit with stale-hash detection,
  whole-file rewrite threshold, whitespace-tolerant fallback (single candidate
  only), per-model edit formats (`str_replace`, `apply_patch`, `hashline` -
  hashline is the default for weak models), post-edit validation with
  automatic rollback (syntax, diff sanity, placeholder/marker/merge-conflict
  checks, optional linter hook), encoding and EOL preservation (UTF-8/BOM/
  cp1252, CRLF/LF), mojibake repair, correct diacritics, emoji-free output.
- **Artifact generation** (assistant work): real PDF/Word/Excel/PowerPoint/CSV
  from markdown-lite or data, seeded synthetic datasets (Faker, pt-BR, valid
  CPF/CNPJ), PNG charts (Pillow, no plotting stack), image transforms,
  LLM-generated imagery via the configured multimodal gateway (`img_llm`,
  with auto photorealism/anti-neon prompt directives), sober curated deck
  themes (KPI cards, tables, timelines, no glossy AI style), synthetic
  scanned/photographed documents for OCR fixtures (seeded, deterministic),
  and satellite tile mosaics as fallback (config-guarded provider). Every
  artifact is read back and verified (`doc_read`) and cited in `finish_task`
  as on-disk evidence. Optional extra: `pip install -e ".[artifacts]"`.
- **Verification harness** (critical): acceptance criteria before work starts,
  repro-first bug fixes, `finish_task` cross-checked against the real
  execution log (zero-evidence finishes are refused), dirty-since-verification
  refusal, `on_stop` verification gate, per-deliverable-type checks
  (tests/lint, run scripts, server smoke, headless browser), anti-cheating
  test-tampering scan, fixed final report format, benchmark suite in
  `benchmarks/`.
- **2026 harness extras**: YAML hook system (session_start / pre_tool_use /
  post_tool_use / pre_compact / stop), LSP-aware error guidance, SWE-agent
  style tool results (windowed file reader, match-listing search, never-empty
  output), grouped tool naming with `response_format`, tool-result compaction
  with task notes, large-output offloading, code-mode tool calls for
  token-starved models, textually-encoded tool calls for weak models, doom-loop
  detection (warn -> stronger model -> stop), planning mode, session search
  (SQLite FTS5), and transient-error resilience: step-level retries with
  backoff for rate limits/5xx/connection resets/timeouts (providers retry
  internally too, with Retry-After HTTP-date support and jitter).
- **Weak-model support**: per-role models (planner/executor/summarizer/
  sub-agent), textual tool protocol with auto-repair (`<<<WOTAN_TOOL>>>`,
  fenced and bare-JSON fallbacks, trailing-comma/unquoted-key repair),
  hashline edits, reduced tool sets.
- **Provider switcher (UI)**: one click in the chat header alternates between
  configured providers (e.g. OpenRouter <-> the corporate gateway); each side
  records its OWN last-used model and restores it on switch (server-side,
  survives restarts, shared across browsers), and models can be saved from
  the picker per provider without touching config.yaml.
- **Corporate gateway integration, update-proof**: OpenAI-compatible,
  Anthropic and **OpenRouter** (one key, hundreds of models; `wotan doctor`
  shows the live model catalog and key credits) providers plus a fully
  declarative `generic_http` provider. Every site-specific adapter lives
  OUTSIDE the package - pure-YAML `generic_http`, or `custom_*.py` files in
  `%USERPROFILE%\.wotan\providers\` (OpenRouter ships ready-made in
  `examples/providers/`) - so upgrading wotan never breaks them
  (Jinja2 request bodies, role mapping, JSONPath/JMESPath response extraction,
  SSE/NDJSON streaming, native or textual tool calls, multimodal
  images/documents), YAML-defined external workflow tools, auth
  (`none` / `api_key` / `oauth_like_token` with single-flight refresh and
  401-retry), `providers/custom_*.py` plugin folder, retry with backoff,
  `wotan doctor` + in-UI "Test connection". A fictional mock gateway
  (`wotan mock`) with expiring tokens is included for offline development.
- **Assistant mode**: non-code end-to-end work, persistent markdown memory,
  self-improving skills (proposed on success, refined on failure), scheduled
  tasks (internal scheduler; Windows Task Scheduler without admin) with an
  inbox and Windows notification, real verification of generated artifacts.
- **Experiment factory**: `wotan.gateway_client` SDK (chat, chat_with_image,
  chat_with_document, extract_json with repair, run_workflow, list_models)
  reusing the same provider YAML and TokenManager, plus a JS/TS mirror
  (`frontend/src/gateway/gatewayClient.ts`); templates (FastAPI webapp, batch
  CSV, Jupyter notebook) and a working entity-extraction example in
  `experiments/`; a private platform-profile skill template in
  `skills/internal-platform/` installed to `%USERPROFILE%\.wotan\skills\`
  (never in the repo); evaluation cases in `evals/`; the IDE's Experiments
  panel runs them on auto-assigned ports with embedded preview.
- **Observability**: JSON logs with correlation ids and rotating files, UI log
  panel, masked raw-LLM trace panel with export, per-session token/cost
  accounting, frontend error shipping, diagnostic bundle zip
  (config with masked secrets, env, doctor output, logs).

## Install (no admin, restricted network)

Requirements: Python 3.11+, Node.js 20+ **only on a build machine** (targets
with npm restrictions install the prebuilt frontend from the GitHub release -
see "Prebuilt frontend" below).

```powershell
git clone <this-repo> wotan
cd wotan
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e .
python scripts\build_frontend.py     # npm install + vite build + embed in wotan/static
```

Optional extras: `pip install -e ".[pty]"` (ConPTY terminals via pywinpty),
`".[keyring]"` (Windows Credential Manager for secrets), `".[pdf]"` (PDF
experiments), `".[artifacts]"` (document/data/image generation:
python-docx, openpyxl, python-pptx, reportlab, pillow, faker, pypdf),
`".[app]"` (native window via pywebview), `".[dev]"` (tests).

## Configure

```powershell
copy config.example.yaml %USERPROFILE%\.wotan\config.yaml
notepad %USERPROFILE%\.wotan\config.yaml
```

YAML is the single source of truth and the Settings screen edits the same
file. Secrets are never stored in YAML: reference `env:VAR` or
`keyring:SERVICE:USER`. See `config.example.yaml` for three ready-made
provider presets (OpenAI-compatible, Anthropic, and a fictional gateway that
matches `wotan mock`) and for how to describe any contract with
`type: generic_http` - request Jinja2 templates, role map, JSONPath/JMESPath
extraction, SSE/NDJSON streaming, token auth - **without code changes**.

Optional agent instructions: `copy AGENTS.example.md <workspace>\AGENTS.md`
(loaded automatically; keep it short).

Optional private platform skill: copy `skills\internal-platform\` to
`%USERPROFILE%\.wotan\skills\internal-platform\` and fill in the template.

## Run

```powershell
wotan                      # server + UI at http://127.0.0.1:8765
wotan --app                # native window (pywebview) with browser fallback
wotan path\to\workspace    # open a specific workspace
wotan doctor               # verify gateway configuration (token, chat, tools, streaming)
wotan mock                 # fictional mock gateway + identity API on :8787
wotan run "explain this repo"   # headless one-shot agent turn
```

`python -m wotan` is equivalent to `wotan`. Single command, no admin, no
external downloads.

### Prebuilt frontend (npm-restricted targets)

GitHub Releases ship a zip containing `wotan/static/` (the built UI). Extract
it into the installed package (or run `pip install wotan` from a wheel built
with the frontend embedded) and `wotan` serves the UI without npm/Node.

## Development

```powershell
pip install -e ".[dev]"
python -m pytest -q            # backend + harness + integration tests
cd frontend && npm install && npx vitest run && npm run dev
```

`npm run dev` proxies API/WS to the backend on `127.0.0.1:8765`
(`wotan` first). Test strategy and deliverable map: `docs/DECISIONS.md`.

## Shortcuts

Ctrl+P quick open, Ctrl+Shift+P command palette, Ctrl+S save, Ctrl+B sidebar,
Ctrl+` terminal, Ctrl+J panel, Ctrl+= / Ctrl+- zoom, Ctrl+0 reset zoom,
Ctrl+N new chat, Ctrl+T stop agent (also a visible Stop button).

## License

MIT - see `LICENSE`.
