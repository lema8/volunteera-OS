---
name: project-exploration
description: Understand an unfamiliar codebase quickly without flooding your context
keywords: explore, codebase, unfamiliar, orient, structure, architecture, onboarding, navigate, understand
version: 1
---

# Project Exploration

## Purpose

Build an accurate mental model of an unfamiliar codebase using the smallest
number of reads, so you can make correct changes without having read everything.

## When to use

Use this skill at the start of any task in a codebase you have not worked in,
or when a task touches an area you have not seen.

## Procedure

1. **Read the ambient summary first.** The system prompt already contains the
   detected languages, build commands and top-level layout. Do not re-derive it.
2. **Read the manifest.** `package.json`, `pyproject.toml`, `Cargo.toml`,
   `go.mod`. This gives you dependencies, scripts, entry points and the
   project's own name for itself.
3. **Read the README** if one exists — but verify its claims against the code;
   READMEs rot.
4. **Map the layout**: `list_directory path="src" depth=2`. Identify where code,
   tests, and configuration live.
5. **Find the entry point** and read it. `main.py`, `src/index.ts`, `main.rs`,
   `cmd/*/main.go`. The entry point reveals the real architecture faster than
   any directory listing.
6. **Search for the feature, not the file.** `search_text` for a string the user
   mentioned (an error message, a UI label, a route, a flag name). This lands you
   in the relevant code immediately.
7. **Use `search_code`** to jump to a symbol's definition rather than guessing
   which file holds it.
8. **Read the tests for the area you are changing.** Tests are executable
   documentation of intended behaviour and they show you the real API.
9. **Check git history for the file** (`git_log path="src/x.py"`) when the
   current code looks strange — recent commits usually explain why.

## Common failures

- **Reading everything.** Reading twenty files "to be safe" burns the context
  window and buys little. Read what the task touches.
- **Guessing paths.** `find_files` and `search_text` cost one call and are
  always right; a guessed path costs a failed read and a wrong assumption.
- **Trusting the README over the code.** Verify commands actually work before
  relying on them.
- **Ignoring the tests.** They tell you the contract you must not break.
- **Missing the build system.** Check for `Makefile`, `justfile`, or scripts
  before inventing your own command line.
- **Assuming conventions.** Match the style, error handling and layout you
  observe, not the style you prefer.

## Verification

Before you start changing code you should be able to state:
- Which file(s) you need to change, and why those and not others
- How the code is built and run
- How it is tested, and the command to run those tests
- Which existing conventions your change must follow

If you cannot answer all four, keep exploring — but with targeted searches, not
bulk reading.

## Examples

**"Fix the login redirect bug" in an unfamiliar Next.js app**

```
read_file package.json                      # scripts, deps -> next, vitest
search_text "redirect" glob=["*.ts","*.tsx"] # 6 hits, one in middleware.ts
read_file middleware.ts
search_code symbol="requireAuth" include_references=true
read_file tests/middleware.test.ts          # the contract
```

Five calls, and you know exactly what to change and how to verify it.
