# Phase 1: local conversation reference layer

## User outcome

A user can find a specific conversation created in one coding agent and point another agent to it. The receiving agent can read a normalized view or follow the preserved original path.

## Boundary

Phase 1 is an index and reference layer, not a memory synthesis system and not a bidirectional synchronization engine.

```text
native session stores + authorized product APIs (read only)
        ↓ adapters
normalized visible messages
        ↓
local SQLite search index
        ↓
CLI · local web UI · MCP
```

## Canonical model

- Session ID: `<source>:<native-session-id>`
- URI: `syncanything://session/<session-id>`
- Session metadata: source, title, working directory, timestamps, original path
- Indexed message: ordinal, role, timestamp, visible text

## Privacy decisions

- No cloud service.
- Explicitly connected cloud products may be pulled through their read-only APIs and cached locally.
- No mutation of source histories.
- No API keys or configuration files are ingested.
- Connector API keys are read from process environment only and are never stored in the index.
- CiteAnything uses a dedicated `context.read` key; the CiteAnything skill's write key is never reused.
- No system/developer prompts, reasoning, tool calls, or tool output are indexed.
- The SQLite index remains sensitive because it contains conversation text.
- Session text returned to an agent is explicitly labeled as untrusted historical context.

## Completion criteria

- Real Claude Code, Codex, and Kimi Code histories index without parser errors.
- Chinese phrase search finds the intended cross-tool conversations.
- A returned result includes its canonical URI and original path.
- MCP clients can list tools, search, read a session, and obtain a reference.
- The local UI can search, filter, inspect, and copy a session reference.
