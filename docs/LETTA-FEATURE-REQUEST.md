# Letta Code Feature Request: Headless Daemon / Serve-Agent Mode

> 日期: 2026-06-23 | 用途: 提交给 Letta 官方

---

## Problem

`letta --new -p "..."` is the only way to programmatically interact with a Letta agent in headless mode. Every invocation:

1. Forks a new Node.js process
2. Loads the agent from disk (MemFS)
3. Initializes the LLM engine
4. Connects to the model provider
5. Runs inference
6. Exits

This takes **~17 seconds** per message (cold start), compared to **2-5 seconds** for frameworks that keep the agent loaded in memory. For use cases that involve frequent, latency-sensitive agent calls (automation, messaging, CI/CD, webhooks), this overhead is prohibitive.

## Proposed Feature: `letta serve-agent`

A subcommand that starts a lightweight HTTP server with the agent pre-loaded:

```bash
# Start agent server (daemon mode)
letta serve-agent --port 8283

# Then call it from scripts/automation
curl -s -X POST http://localhost:8283/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is the current time?", "conversation_id": "optional-existing-conv"}'
```

### Expected behavior

| Feature | Description |
|:--|------|
| **Pre-loaded agent** | Agent context (system prompt, memory, skills) loaded once at startup, not re-loaded per request |
| **HTTP API** | REST endpoint for sending prompts and receiving replies |
| **Conversation isolation** | Support `--new` equivalent via `conversation_id` parameter (or auto-create) |
| **Headless** | No TUI, no interactive mode — pure JSON/HTTP |
| **Graceful shutdown** | `SIGTERM` / `SIGINT` saves state and exits cleanly |
| **Optional auth** | Local-only by default (127.0.0.1); optional token for remote access |

### CLI reference

```bash
letta serve-agent [options]

Options:
  --port <n>          HTTP listen port (default: 8283)
  --host <addr>       Bind address (default: 127.0.0.1)
  --agent <id>        Agent ID (default: current agent)
  --model <model>     Override model
  --no-cors           Disable CORS headers

Examples:
  letta serve-agent                           # Start on default port
  letta serve-agent --port 9090               # Custom port
  letta serve-agent --agent agent-abc123      # Specific agent
```

### API endpoints

```
POST /v1/chat
  Request:  {"prompt": "...", "conversation_id": "optional"}
  Response: {"reply": "...", "conversation_id": "conv-xyz", "tokens": {...}}

GET  /v1/health
  Response: {"status": "ok", "agent_id": "...", "uptime_seconds": 123}

POST /v1/conversations
  Request:  {}  (creates new conversation)
  Response: {"conversation_id": "conv-xyz"}
```

## Why This Matters

- **Latency**: 17s → 2-5s per message (eliminates cold start)
- **Throughput**: Multiple concurrent connections instead of one-per-process
- **Resource efficiency**: One Node.js process vs. one per message
- **SIGPIPE risk**: HTTP (TCP) replaces POSIX pipe, eliminating SIGPIPE/rc=141 issues entirely
- **Parity**: Hermes, OpenClaw, Claude Code (MCP server), and Goose all offer daemon/HTTP modes. Letta Code should too.

## Non-Goals (out of scope for v1)

- WebSocket streaming (can be added later)
- Multi-agent management
- Remote/cloud deployment
- Authentication beyond local-only

## Reference

Similar patterns in other frameworks:
- **Hermes**: `hermes-agent` daemon + `hermes chat -q` CLI
- **OpenClaw**: `openclaw gateway` daemon + `openclaw -p` CLI
- **Claude Code**: MCP server mode for programmatic access
- **Goose**: MCP + daemon architecture

---

> Submitted by a Letta Code user who needs headless automation at scale.
> No project-specific details included — this is a general-purpose feature request.
