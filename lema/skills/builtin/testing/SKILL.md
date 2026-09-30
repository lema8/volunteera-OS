---
name: testing
description: Find, run and write tests for a project, and interpret the results honestly
keywords: test, tests, pytest, jest, vitest, cargo, go, unittest, coverage, ci, tdd, verify
version: 1
---

# Testing

## Purpose

Verify that code actually works, using the project's own test tooling, and write
tests when coverage for the changed behaviour is missing.

## When to use

Use this skill after any code change, and whenever the user mentions tests,
coverage, CI, or asks whether something works.

## Procedure

1. **Identify the runner.** Look for the marker file, then the config:
   - `pyproject.toml` / `pytest.ini` / `tox.ini` → `pytest`
   - `package.json` → read `scripts.test` (jest, vitest, mocha, node --test)
   - `Cargo.toml` → `cargo test`
   - `go.mod` → `go test ./...`
   - `pom.xml` → `mvn test`; `build.gradle` → `./gradlew test`
   - `Makefile` with a `test:` target → `make test`
2. **Run the existing suite first, before changing anything.** You need a
   baseline: pre-existing failures are not yours, and you must not be blamed for
   them or mistake them for your own breakage.
3. **Make your change.**
4. **Run the focused test** for what you changed (fast feedback):
   - `pytest tests/test_x.py::test_name -x -q`
   - `npx vitest run tests/x.test.ts -t "name"`
   - `cargo test test_name`
   - `go test -run TestName ./pkg/...`
5. **Run the full suite** once the focused test passes.
6. **Compare with the baseline.** Any *new* failure is yours; fix it.
7. **Add tests for new behaviour.** Put them where the existing tests live, match
   the surrounding style, and cover the happy path plus at least one edge case
   or error path.
8. **Confirm the new test actually tests something** by making it fail once
   (temporarily break the code or the assertion), then restore.

## Common failures

- **`ModuleNotFoundError` for the project itself** — the package is not
  installed. Use `pip install -e .`, or run with `python -m pytest`, or set
  `PYTHONPATH=.`.
- **Tests pass locally but the change is untested** — you wrote a test that
  asserts nothing, or it was never collected. Check the collected count.
- **Flaky tests** — time, randomness, network, ordering, or shared state. Run the
  single test repeatedly (`pytest --count=10` / `go test -count=10`) to confirm.
  Do not "fix" a flake by adding a sleep without understanding it.
- **Slow full suite** — use the focused test during iteration; run everything
  once at the end. Never skip the final full run.
- **Wrong interpreter/venv** — `which python`, `python -V`, `node -v`. A missing
  dependency is usually really a wrong environment.
- **Snapshot mismatches** — read the diff before regenerating. Blindly running
  `-u`/`--update-snapshots` erases the signal.

## Verification

- The test command exits 0.
- The number of passing tests increased if you added tests.
- The test fails when the behaviour it covers is broken (you checked).
- No test was deleted, skipped or weakened to make the suite green. If you had
  to change an assertion, state why explicitly in your summary.

## Examples

**Python**

```bash
pytest -q                                   # baseline
pytest tests/test_auth.py::test_login -x -q # focused, after the change
pytest -q                                   # full suite
```

**Node**

```bash
npm test                     # baseline (reads scripts.test)
npx vitest run src/auth.test.ts
npm test
```

**Rust**

```bash
cargo test --quiet
cargo test auth::tests::login -- --nocapture
```
