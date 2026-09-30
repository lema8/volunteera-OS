---
name: debugging
description: Systematically diagnose and fix a failure instead of guessing at patches
keywords: debug, error, bug, crash, traceback, stacktrace, failure, diagnose, fix, investigate
version: 1
---

# Debugging

## Purpose

Find the actual cause of a failure and fix that, rather than applying plausible
looking patches until the symptom disappears.

## When to use

Use this skill when:
- A command, test or build fails
- Behaviour differs from what is expected
- An exception, stack trace or compiler error appears
- Something worked before and does not now

## Procedure

1. **Reproduce it.** Run the failing thing yourself and capture the real output.
   Never debug from a description alone. If you cannot reproduce it, find out
   what differs about the environment before changing any code.
2. **Read the error properly.** The bottom of a Python traceback and the *first*
   error of a compiler cascade are the informative ones. Extract: the exact
   message, the file, and the line.
3. **Read that line and its surroundings.** `read_file` with the reported line
   number. Do not assume you know what the code says.
4. **Form one hypothesis.** State it explicitly: "`config` is None here because
   `load()` returns early when the file is missing."
5. **Test the hypothesis cheaply** before changing anything: add a print/log,
   run a one-liner in the REPL, `search_text` for the other call sites, or check
   the value with a debugger. Confirm or reject.
6. **If rejected, go back to step 4.** Do not stack speculative fixes.
7. **Fix the cause.** Once confirmed, make the smallest change that addresses
   the root cause, not the symptom.
8. **Re-run the original failing command.** It must now pass.
9. **Run the wider test suite.** Confirm you did not break something else.
10. **Consider whether a regression test is warranted.** If the bug could
    silently return, add one.

## Common failures

- **Shotgun debugging.** Changing several things at once, so you cannot tell what
  helped. Change one thing per iteration.
- **Fixing the test instead of the code.** Only weaken an assertion if the
  assertion itself is genuinely wrong, and say so explicitly.
- **Trusting a stale build.** Rebuild/reinstall before concluding a fix failed —
  especially with compiled languages, caches, or `pip install -e`.
- **Ignoring the first error.** In compilers and type checkers, later errors are
  usually consequences of the first.
- **Wrong environment.** Confirm which interpreter/venv/node version is actually
  being used (`which python`, `python -V`) before blaming the code.
- **Re-running the identical failing command.** It cannot produce a different
  result. Change something.

## Verification

- The originally failing command now exits 0.
- The full test suite passes (or fails only in ways that were already failing —
  verify that by checking out the prior state if unsure).
- You can explain in one sentence *why* the bug happened. If you cannot, you
  probably patched a symptom.

## Examples

**A failing test**

```
$ pytest tests/test_parser.py::test_nested -x
E   KeyError: 'children'
tests/test_parser.py:42: in test_nested
src/parser.py:118: in walk
```

1. Read `src/parser.py` around line 118.
2. Hypothesis: leaf nodes have no `children` key.
3. Test it: `search_text "children" src/parser.py` shows the key is only set in
   `_make_branch`.
4. Fix: `node.get("children", [])` at the call site.
5. Re-run: `pytest tests/test_parser.py -x` → passes.
6. Re-run everything: `pytest` → passes.

**A failing build**

```
$ npm run build
Module not found: Can't resolve '@/lib/utils'
```

1. Hypothesis: the `@` path alias is not configured.
2. Check: `read_file tsconfig.json` → no `paths` entry.
3. Fix: add `"paths": {"@/*": ["./src/*"]}` under `compilerOptions`.
4. Verify: `npm run build` succeeds.
