---
name: code-writer
description: "Delegate boilerplate code generation to a worker model. Use for tests, config, docstrings, type stubs, or any generation where >80% is predictable from reference files."
---

Call the `code_write` tool:

```
# Generate and write directly to target file
code_write --spec "<what to generate>" --reference <reference-file> --target <output-path>

# Output to stdout instead (omit --target)
code_write --spec "<what to generate>" --reference <reference-file>
```

Each call is independent. To build on what was just generated, pass that file as the
`--reference` for the next call.

Review the output and make surgical edits for the ~5-20% that needs orchestrator-level judgment.
