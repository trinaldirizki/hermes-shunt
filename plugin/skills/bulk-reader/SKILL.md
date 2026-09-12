---
name: bulk-reader
description: "Delegate bulk file reading to a worker model. Use for files >350 lines, questions across 3+ files, or summarizing large diffs."
---

Call the `bulk_read` tool:

```
bulk_read --question "<question>" --paths <file1> [<file2> ...]
```

Each call is independent. To ask a follow-up, ask again with the same paths — the files
go to the worker, never into your context, so re-sending them costs you nothing.

Verify specific line numbers or exact values before using them in edits.
