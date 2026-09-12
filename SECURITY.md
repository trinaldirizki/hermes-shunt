# Security notes

hermes-shunt runs **in-process inside your Hermes agent host**. Treat it with
the same trust as any Hermes plugin: it executes Python in the agent process,
not in a sandbox. This document describes what it does and does not touch, so
a reviewer can assess it like any third-party dependency.

## What the plugin can do (trust surface)

| Capability | Used for | Boundaries |
|---|---|---|
| Read any file the agent can read | `bulk_read` assembles the corpus before delegation | Paths must exist & be readable, validated pre-call; corpus is size-capped (`SHUNT_MAX_CORPUS_TOKENS`) |
| Write files (`code_write --target`) | Writing generated boilerplate to disk | Same authority as any Hermes tool write; paths are explicit arguments |
| One `ctx.llm.complete()` per delegation | Worker call on your configured provider | Trust-gated: model/provider overrides require explicit `plugins.entries.hermes-shunt.llm.*` grants; without them the call stays on the active model and override attempts fail loud |
| `pre_tool_call` hook | Blocking >350-line full reads and bare `cat/head/tail/less/more` | Observe + block only; it never mutates args, results, or the system prompt; `SHUNT_DISABLE_HOOK=1` kills it live |

## What it never does

- No network calls of its own — all LLM traffic goes through the host's
  `ctx.llm` (your provider config, your credentials; the plugin never sees keys)
- No subprocesses, no shell, no shellcode (bash-read detection is pure
  `shlex` parsing of the command string)
- No telemetry, no outbound webhooks, no analytics, no file access outside
  explicit `paths`/`target` arguments
- No dependencies beyond the Python standard library (stdlib-only; verify:
  `grep -hE "^(import|from)" plugin/*.py`)
- Stats are local-only (`~/.hermes/hermes-shunt-stats.jsonl`)

## Data flow

```
file contents -> worker LLM prompt (your provider, your account)
             <- distilled answer -> agent conversation
```

If your organization considers source code sent to an LLM provider a data
egress, it happens here exactly once per delegation, through the same
provider account the agent already uses. Routing delegation to a different
provider requires the explicit trust gates above.

## Supply-chain notes

- 100% of the runtime code is in this repository (`plugin/`, ~600 LOC, stdlib
  only) — no transitive dependencies to audit beyond Hermes itself
- History: single author, rewritten to `@users.noreply.github.com`; full-tree
  scans for secrets/organizational references across all commits came back
  clean at publication time
- The evaluation fixtures under `bench/` are vendored from
  spotify/portal-ai-plugins (Apache-2.0) — synthetic files, no real code

## Reporting

Open an issue at the repository, or contact the maintainer via GitHub.
