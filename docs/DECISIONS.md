# Wotan - architecture and decision log

## Architecture

```
wotan/ (Python package)
  cli.py               wotan / wotan --app / doctor / mock / run
  server.py            FastAPI: REST + WebSocket (agent, terminals) + static UI
  config.py            YAML schema (Pydantic), ${VAR} env interpolation, keyring
  db.py                SQLite (sessions, messages, checkpoints, todos, inbox, FTS5)
  providers/           base, openai_compat, anthropic_provider, generic_http,
                       text_tools (weak-model textual calls), registry, errors
  auth/token_manager   oauth_like_token: single-flight refresh, expiry, 401 retry
  editing/             encodings, checkpoints, validators, engine (edit gate)
  tools/               18 tools + aliases, ToolError format, sensitive-path guard
  agent/               session loop, permissions, verification gate, hooks,
                       doomloop, prompts, memory, skills, repo_map, scheduler,
                       websearch, execution_log
  gateway_client/      experiment SDK reusing providers + TokenManager
  terminal/            ConPTY (pywinpty) on Windows, stdlib pty elsewhere
  mock_gateway.py      fictional contract + expiring tokens + fault triggers
  doctor.py            per-provider probe (token, message, tool_call, streaming)
frontend/ (React + TypeScript + Vite)
  src/editor/          EditorAdapter: Monaco -> CodeMirror 6 -> textarea
  src/components/      IDE panels, chat, tool cards, approvals, status bar
  src/gateway/         gatewayClient.ts (JS mirror of the SDK via /gw proxy)
```

## Key decisions

1. **FastAPI serves UI + API + WS from one process** - one start command, one
   port, no separate static server (corporate-friendly).
2. **Editor fallback chain** behind one `EditorAdapter` interface
   (Monaco -> CodeMirror 6 -> textarea). Monaco/CodeMirror/workers/fonts/icons
   are bundled; nothing loads from a CDN at runtime.
3. **Config is YAML-only** (`~/.wotan/config.yaml` + workspace `.wotan/config.yaml`
   deep-merge, `${VAR}`/`${VAR:-default}` interpolation). Secrets are
   `env:`/`keyring:` references, never values in YAML. The Settings screen
   edits the same file.
4. **Gateway integration is data, not code**: `generic_http` describes request
   (Jinja2 body templates, role map) and response (JSONPath -> manual
   `[?(@.k==v)]` filter -> JMESPath extraction, SSE/NDJSON/none streaming,
   tool-call shape). Presets prove it: `openai_compatible` and `anthropic`
   exist, and the fictional `generic_http` preset reproduces a third contract
   that matches `wotan mock`. `providers/custom_*.py` plugins cover the rest.
   Token auth: `none` / `api_key` / `oauth_like_token` (single-flight refresh,
   seconds/epoch/jwt/ttl expiry, margin-based proactive refresh, 401-retry).
5. **Edits are structured JSON only** (`path/old_string/new_string/replace_all`).
   Exactly-once `old_string`, atomic `multi_edit`, read-before-edit with
   stale-hash, rewrite threshold (40 lines), whitespace-tolerant fallback only
   with a single candidate (indent + EOL adapted to the file). Post-edit
   validators (Python syntax, no-degradation diff, placeholder/marker/merge
   checks, linter hook) trigger automatic rollback. Encodings preserved
   (UTF-8/BOM/cp1252 detected by BOM + utf-8 strict + cp1252 heuristic, codec
   recorded per file); EOL preserved on edit, `new_file_eol` for new files.
   `write_file` of large deletions refused; emoji/diacritics policy enforced.
   Per-model `edit_format`: `str_replace` (default), `apply_patch`, `hashline`
   (sha1-line anchors; default for `weak: true` models).
6. **Verification gate**: `finish_task` is cross-checked against the
   `runs` execution log (run_id, exit codes, output hashes; zero evidence is
   refused), dirty-since-verification is refused (tracked per tool call and by
   hook verdicts), `on_stop` runs AGENTS.md/config verification commands and
   blocks stop on failure. `scan_test_tampering` flags skipped/deleted tests,
   weakened asserts. Evidence types map to real commands (tests/lint, `--check`
   scripts, server smoke request, headless browser check). The final report is
   a fixed format (Verified / Not verified / Known issues / How to test).
7. **Weak-model resilience**: textual tool protocol
   (`<<<WOTAN_TOOL>>>` JSON marker, fenced `tool_call`, bare-JSON scanner with
   balanced-brace parse), JSON repair (trailing commas, single quotes,
   unquoted keys, Python literals), auto-repair loop with error feedback,
   hashline edits, reduced tool sets per model, doom-loop detector
   (4+ same-file edits, repeated identical calls, consecutive errors ->
   escalate to a stronger model -> stop with a clear message).
8. **Terminal**: ConPTY via pywinpty on Windows (no admin), stdlib `pty` on
   POSIX. WebSocket bridge, xterm.js UI with FitAddon, multiple terminals.
9. **Agent observability**: JSON logs with correlation ids, rotating files,
   `read_app_logs` tool, diagnostic bundle (zip with masked config/doctor/logs),
   masked raw-LLM trace panel with export, per-session token/cost.
10. **Security**: permission modes (`ask` / `edits` / `autonomous`), destructive
    command approval with allow/deny lists, workspace-restricted FS (path
    traversal blocked), per-step checkpoints with undo + diff review, web
    content delimiting + Rule-of-Two taint, secret scanning with masked logs,
    protected `.env`/key files ("fix the code, not the config"), `--no-verify`
    blocked even in autonomous mode.
11. **Sessions and memory**: SQLite (`wotan.sqlite3`) with FTS5 `search_sessions`;
    AGENTS.md auto-read (goal/conventions/verify parsed), Agent Skills
    3-phase loading (frontmatter listing -> SKILL.md -> scripts/references/
    assets on demand) from the workspace and `%USERPROFILE%\.wotan\skills\`,
    personalized-PageRank repo map (tree-sitter when available, regex fallback)
    with binary-search token budgeting, compaction that preserves decisions and
    task notes, persistent markdown memory (`~/.wotan/memory/`).
12. **Experiments**: `gateway_client` SDK reuses provider YAML + TokenManager
    (chat, chat_with_image, chat_with_document, extract_json with repair loop,
    run_workflow, list_models). Templates (FastAPI webapp, batch CSV, notebook)
    and a mock-verified entity-extraction example ship in `experiments/`.
    Generated apps are tested for real before the agent reports done. The
    private platform-profile skill ships only as a template; real copies live
    in `%USERPROFILE%\.wotan\skills\`.
13. **Windows-first without admin**: PowerShell default shell (ConPTY),
    cmd/Git Bash options, Windows Task Scheduler via `schtasks` (user tasks,
    no admin) behind the same scheduler interface as the built-in one,
    Windows toast notification best-effort.

## Testing

- `tests/` - pytest: edit engine (CRLF/BOM/cp1252/tabs/repeats/large files/
  syntax breakage/whitespace mismatch/markers/placeholders/concurrent edits),
  provider contract parsing + streaming + retry, token manager, verification
  gate + tamper detection, hooks/permissions/doomloop, tools + textual tool
  protocol, gateway SDK + mock gateway + generated-app run, terminal PTY,
  server REST/WS smoke, security rules, repo map/token budget/memory/skills,
  doctor, config round-trip, entity-extraction experiment end-to-end.
- `frontend/src/test/` - vitest: editor fallback chain + textarea adapter,
  tool cards, model picker grouping, tab store, diff rendering.
- `benchmarks/` - 24-task harness benchmark (fixtures + per-task
  acceptance criteria + runner that scores evidence-checked completion).

## Deliverable map

| Area | Where |
| --- | --- |
| 1 Core + IDE | `wotan/server.py`, `wotan/cli.py`, `wotan/terminal/`, `frontend/` |
| 2 Gateway | `wotan/providers/`, `wotan/auth/`, `wotan/mock_gateway.py`, `wotan/doctor.py`, `wotan/gateway_client/`, `frontend/src/gateway/` |
| 3 Editing | `wotan/editing/`, `tests/test_edit_*.py` |
| 4 Verification | `wotan/agent/verification.py`, `execution_log.py`, `benchmarks/` |
| 5 Harness extras | `wotan/agent/{hooks,doomloop,prompts}.py`, `wotan/tools/__init__.py`, `wotan/providers/text_tools.py` |
| 6 IDE | `frontend/src/components/`, `frontend/src/editor/` |
| 7-8 Agent UX | `wotan/agent/session.py`, `frontend/src/components/ChatPanel.tsx` |
| 9 Tools | `wotan/tools/__init__.py` |
| 10 Security | `wotan/security.py`, `wotan/agent/permissions.py`, `editing/checkpoints.py` |
| 11 Memory | `wotan/agent/{memory,skills,repo_map}.py`, `wotan/db.py` |
| 12 Weak models | `providers/text_tools.py`, `editing` hashline, `agent/doomloop.py` |
| 13 Observability | `wotan/logging_setup.py`, `wotan/security.py` (masking), trace panel |
| 14 Chat UX | `frontend/src/components/`, `state/store.ts` |
| 15 Assistant mode | `wotan/agent/{memory,scheduler}.py`, `frontend/src/components/AssistantPanel.tsx` |
| 16 Experiment factory | `wotan/gateway_client/`, `experiments/`, `skills/internal-platform/`, `evals/` |

7. **Artifact generation is a tool layer, not code the agent writes**: the
   model calls doc_pdf/doc_docx/doc_xlsx/doc_pptx/data_csv/data_synthetic/
   data_chart/img_transform/img_satellite instead of producing throwaway
   scripts. Libraries are the established ones (python-docx, openpyxl,
   python-pptx, reportlab, pillow, faker, pypdf) and live behind the optional
   `artifacts` extra; without it every tool degrades to ERROR/WHY/HOW TO FIX.
   All outputs stay inside the workspace (same `_inside` policy as fs tools).
   Documents share one markdown-lite parser (`wotan/artifacts/common.py`) so
   PDF and Word render the same source; `doc_read` reads artifacts back and
   the verification gate treats verified artifacts as hard finish evidence
   (existence + size + format header + text extraction) - document tasks can
   finish honestly without a test suite.
8. **Satellite imagery is config-guarded**: `img_satellite` fetches a slippy
   map tile mosaic from a provider URL template in
   `artifacts.satellite.base_url`, with a host allow-list (SSRF guard), a
   realistic tile cap and embedded attribution. No arbitrary URL fetches.
9. **Transient-failure resilience is layered**: (a) providers retry 401
   refresh-once, 429 (Retry-After: seconds OR HTTP-date - parsed defensively
   after a real ValueError crash), 5xx (500 included), timeouts and
   httpx.TransportError with capped, jittered backoff
   (`providers/http_retry.py`); (b) the session retries retryable LLM errors
   per step (`limits.llm_step_retries`, default 2) with visible warnings and
   cancels cleanly on stop; (c) non-retryable errors fail once, fast.
10. **Synthetic scans are seeded pipelines, not random filters**
    (`artifacts/scandoc.py`): a clean render pass (markdown-lite and/or a
    structured form spec with handwriting-look field values, checkboxes, a
    seed-generated signature squiggle and a rotated muted stamp) followed by a
    deterministic degradation chain (paper tint, rotation, perspective skew +
    vignette in photo mode, seeded sensor noise, blur, JPEG re-compression).
    `Image.effect_noise` is avoided because its RNG is unseedable - noise is
    drawn from the seeded `random.Random` (half-resolution + upscale).
    Output: PNG/JPG per page or an image-backed PDF (`doc_scan_image` /
    `doc_scan_pdf`) - exactly what OCR pipelines ingest.
11. **Images come from the multimodal gateway, not from image banks**
    (`img_llm` -> `artifacts.image_generation.{provider_id, model}`): the
    session reuses the provider's TokenManager auth and posts to the
    OpenAI-compatible `/images/generations` contract (several response shapes
    accepted). Prompt guidance auto-appends photorealism directives for people
    (natural light, real skin texture, 85mm look - explicitly anti
    blurry-AI-people) and sober minimal-editorial directives for
    diagrams/infographics (desaturated palette, whitespace, thin lines -
    explicitly anti neon-AI style). `img_satellite` (tile mosaic) remains as
    the configured-provider fallback for aerial imagery.
12. **Deck design is curated, data-driven** (`artifacts/pptx.py`): muted
    themes (executive/nordic/editorial/graphite/terra), thin accent rules,
    dash bullets, footer + page numbers, and rich layouts (agenda, KPI cards,
    native table, timeline, chart, image with caption, takeaway lines) built
    shape-by-shape so any engine renders them - no template-name dependency,
    no gradients, no glossy fills.
