---
name: internal-platform
description: Knowledge about the internal generative AI platform (corporate LLM gateway): endpoints, contract quirks, recommended models, workflows and internal rules. Use whenever a request involves generative AI, the platform by name, or experiments that call the gateway.
aliases: [PLATFORM_NAME, "use the platform", "the gateway"]
---

# Internal platform profile (PRIVATE SKILL - TEMPLATE)

> This is a template. Copy this folder to `%USERPROFILE%\.wotan\skills\internal-platform\`
> (outside the repository - never commit it) and fill in the placeholders.
> The public repository ships only this template; no company data appears here.

## Platform identity

- Platform name (also add chat aliases above): `PLATFORM_NAME`
- What it is for: `ONE_LINE_DESCRIPTION`

## Endpoints

| Purpose | URL | Notes |
| --- | --- | --- |
| Chat / generate | `GATEWAY_BASE_URL/ENDPOINT` | see `~/.wotan/config.yaml` providers block |
| Identity token | `IDENTITY_URL` | oauth_like_token |
| List models | `LIST_MODELS_URL` (if any) | auto-discovery below |
| List workflows | `LIST_WORKFLOWS_URL` (if any) | auto-discovery below |

## Contract quirks

- REQUEST_SHAPE_NOTES (e.g. "system prompt goes in system_hint, not in messages")
- RESPONSE_SHAPE_NOTES (e.g. "text is at prediction.output_text")
- TOOL_CALL_NOTES (e.g. "native actions work; textual fallback for model X")
- LIMITS (max tokens, file sizes, rate limits)

## Recommended models

| Task | Model id | Why |
| --- | --- | --- |
| Planning / code | `MODEL_STRONG` | tool calling, large context |
| Cheap execution | `MODEL_CHEAP` | fast, cheap |
| Vision | `MODEL_VISION` | accepts images |
| Documents | `MODEL_DOCS` | accepts PDFs |

## Workflows (external_tools)

- `workflow_name` - what it does, inputs, outputs.

## Internal rules

- Data classification limits (what may be sent to the platform).
- Approval requirements, naming conventions, cost centers.

## Auto-discovery

If the endpoints for listing models or workflows exist, ask the agent to run
`scripts/discover.py` (or just ask in chat: "discover platform models"), review
the proposed updates, and approve - the skill is updated in place.

1. Query the model-listing endpoint with the configured auth.
2. Query the workflow-listing endpoint.
3. Propose table updates for this SKILL.md (never auto-overwrite without approval).

## Filling this template

1. Copy the folder to `%USERPROFILE%\.wotan\skills\internal-platform\`.
2. Replace every `PLACEHOLDER` in backticks.
3. Delete this section.
4. Verify: ask Wotan "use <platform> to summarize this file" - it should load
   this skill and use the configured gateway (test with `wotan doctor` first).
