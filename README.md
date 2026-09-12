hermes-shunt
===========

Shunts I/O-heavy work (large file reads, boilerplate generation) to one-shot
worker LLM calls, so file corpora never enter the orchestrator's context.

Hermes port of Spotify's [shunt](https://github.com/spotify/portal-ai-plugins)
Claude Code plugin. Routing semantics, allow-rules, eval case matrix, and mode
instruction texts ported from upstream (Apache-2.0) — see NOTICE.

Status: v0.1.0 scaffold (Task 1). Hook + tools land in Task 4; evals in Task 5.

## Install (development)

```bash
ln -s ~/hermes-shunt/plugin ~/.hermes/plugins/hermes-shunt
hermes plugins enable hermes-shunt
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SHUNT_MIN_LINES` | `350` | Line count above which full-file reads are blocked and redirected |
| `SHUNT_TIMEOUT_SECONDS` | `180` | Timeout for one delegation call |
| `SHUNT_MAX_CORPUS_TOKENS` | `100000` | Estimated-token guard (chars/4) on the assembled corpus — must fit the worker model's context window with answer headroom |
| `SHUNT_WORKER_MODEL` | unset → active model | Worker model (requires `plugins.entries.hermes-shunt.allow_model_override`) |
| `SHUNT_WORKER_PROVIDER` | unset → active provider | Worker provider (requires `allow_provider_override`) |
| `SHUNT_DISABLE_HOOK` | unset | `1` = kill-switch: disable blocking without uninstalling |

Worker-model routing (e.g. to a cheap model) additionally requires the trust
gates in `~/.hermes/config.yaml`:

```yaml
plugins:
  entries:
    hermes-shunt:
      allow_model_override: true
      allowed_models: [deepseek-chat]
```

Without the gates, delegation runs on the active model (zero-config) and
routing raises `PluginLlmTrustError`.

## /shunt stats

In-session slash command (interactive CLI/TUI and gateway sessions —
one-shot `hermes chat -q` has no slash dispatch, so `/shunt` reaches the
model as plain text there):

```
/shunt stats
calls: 2 (bulk-reader:2)
worker tokens spent: 9,553
orchestrator tokens avoided: 17,672
```

## What does not get delegated

- Debugging — requires the orchestrator's reasoning, not a summary
- Editing — exact content must be in context; use targeted reads (offset/limit)
- Small files — delegation overhead exceeds savings under 350 lines
- Architectural decisions — judgment stays on the orchestrator

## License

Apache-2.0. Mode instruction texts and routing semantics ported from
spotify/portal-ai-plugins (Apache-2.0) — see NOTICE.
