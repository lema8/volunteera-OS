---
name: git-workflow
description: Inspect repository state safely and perform common git operations without losing work
keywords: git, commit, branch, diff, stash, merge, rebase, conflict, revert, status, history
version: 1
---

# Git Workflow

## Purpose

Use git to understand what changed, protect uncommitted work, and perform common
operations without destroying anything.

## When to use

Use this skill when working in a git repository — especially before any
destructive operation, when the user asks about changes, or when resolving a
conflict.

## Procedure

### Before doing anything destructive

1. `git_status` — what is staged, modified, untracked?
2. `git_diff` — what exactly would be lost?
3. If there is uncommitted work you did not create, stop and tell the user
   before proceeding.

### Understanding what changed

1. `git_status` for the overview.
2. `git_diff` for unstaged changes; `git_diff staged=true` for the index.
3. `git_diff ref="main"` to compare against a branch.
4. `git_log limit=10` for recent history.
5. `git_show ref="<hash>"` for one commit in detail.

### Committing (only when explicitly asked)

1. Review `git_diff` — never commit changes you have not looked at.
2. Check for secrets, debug prints, and stray files in the diff.
3. Stage deliberately: prefer `paths=[...]` over `add_all=true`.
4. Write a message that says *why*, not *what the diff already shows*.
5. `git_commit message="..."` and confirm the reported file list is what you
   intended.

### Branching

1. `git_status` first — switching with uncommitted changes can fail or carry
   them across.
2. `git_branch name="feature/x" create=true`.

### Resolving a conflict

1. `run_command "git status --short"` — conflicted files are marked `UU`.
2. `read_file` each one and locate the `<<<<<<<` / `=======` / `>>>>>>>` markers.
3. Resolve by understanding both sides; do not blindly take one.
4. Remove every marker. `search_text "<<<<<<<"` to prove none remain.
5. Build and test before continuing the merge/rebase.
6. `run_command "git add <file>"`, then `git rebase --continue` or
   `git merge --continue`.

## Common failures

- **Committing without being asked.** Do not. Commit only on explicit
  instruction.
- **`git add -A` sweeping in junk** — build artifacts, `.env`, large files.
  Check `git_status` for untracked files first.
- **`git checkout .` / `git reset --hard`** — these permanently discard
  uncommitted work. Never run them unless the user explicitly asks, and say what
  will be lost first.
- **Detached HEAD confusion** — `git_status` reports it; `git switch -` returns.
- **`Please tell me who you are`** — git identity is unset. Ask the user rather
  than inventing an identity.
- **Force pushing** — never without an explicit instruction, and prefer
  `--force-with-lease`.
- **Conflict markers left in the file** — the build will fail with a syntax
  error in a place that makes no sense. Always grep for `<<<<<<<`.

## Verification

- `git_status` shows the state you intended.
- `git_log limit=1` shows your commit with the right message.
- `git_diff` is empty after committing everything you meant to commit.
- The project still builds and tests pass after a merge or rebase.

## Examples

**Summarise what this session changed**

```
git_status
git_diff stat_only=true
git_diff                      # read the actual hunks
```

**Safe commit of specific files**

```
git_status
git_diff
git_commit message="fix: handle empty config file in loader" paths=["src/config.py", "tests/test_config.py"]
git_log limit=1
```
