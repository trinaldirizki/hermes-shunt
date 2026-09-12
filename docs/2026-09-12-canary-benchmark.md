# hermes-shunt benchmark — public scenarios (2026-09-12)

Worker: `deepseek-chat` → served as `deepseek-flash` (DeepSeek alias), via
trust-gated `ctx.llm` routing. Scenarios mirror upstream shunt's benchmark
shapes (single large file / multi-file comprehension / code-write). All token
numbers are **measured** from `~/.hermes/hermes-shunt-stats.jsonl`.

Corpora: the upstream fixture `websocket-handler.ts` (602 lines, Apache-2.0,
`bench/`) for the large-file scenario; the public repo
[trinaldirizki/devkit](https://github.com/trinaldirizki/devkit) for the
multi-file and code-write scenarios.

## Results

| Scenario | Corpus | Orchestrator without | Orchestrator with | Context saved | Worker one-shot |
|---|---|---|---|---|---|
| Single large file (`websocket-handler.ts`, 602 L) | 12,048 tok | 12,048 | ~350 | **12,048 → ~350 (97%)** | 13,290 |
| Multi-file comprehension (4 devkit files, 593 L) | 6,210 tok | 6,210 | ~550 | **6,210 → ~550 (91%)** | 8,707 |
| Code-write (devkit-pattern base64 module) | 1,751 tok ref | generation in-context | 40 L to disk | generation offloaded | 3,593 |

Answers verified against ground truth: the websocket answer correctly reported
the fixture has no event enum (only state-transition comments, cited by line);
the devkit answer correctly reverse-engineered the registry pattern (pure
functions, typed discriminated-union results, `lib/config/tools.ts` central
registry, command palette consuming slug/keywords); the generated `base64.ts`
follows the repo's tool-module pattern (typed results, pure functions,
examples object).

## The honest trade

Single-shot totals are **higher** with shunt (the worker re-receives the
corpus as its prompt): e.g. large-file 13,290 worker tokens vs 12,048 saved.
The savings are not single-call — they are:

1. **Context-window**: 91–97% of the corpus never enters the orchestrator's
   window (measured above), and
2. **Compounding**: the corpus stops re-riding every subsequent turn. At 5
   turns, the large-file scenario avoids 12,048×5 − (350×5 + 13,290) ≈
   **44,300 tokens (73%)**; multi-file avoids ≈ 24,300 (70%).

Worker spend bills at deepseek-flash rates (~$0.3/M input ≈ well under a cent
per call at these sizes) in a separate window, while orchestrator context
bills at your main model's rates every turn.

## Method notes

- Token counts are the plugin's own chars/4 estimate for corpus and recorded
  `input_tokens`/`output_tokens` for worker usage — not provider-inflated.
- Baseline "orchestrator without" = reading the same files into the
  orchestrator context (what the hook blocks).
- Earlier read-only canary runs on a private work repo (not reproduced here)
  showed the same 96–97% context savings on 950- and 2,100-line corpora;
  numbers omitted for confidentiality — the public scenarios above stand on
  their own.
