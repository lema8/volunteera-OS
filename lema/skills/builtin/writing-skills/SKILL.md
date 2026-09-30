---
name: writing-skills
description: Write and improve Lema skills so they are reusable, concrete and trustworthy
keywords: skill, create_skill, update_skill, procedure, documentation, meta, reusable
version: 1
---

# Writing Skills

## Purpose

Produce skills that are genuinely reusable procedural knowledge, so that a future
session (or a weaker model) can follow them and succeed without rediscovering
everything.

## When to use

Use this skill when:
- You are about to call `create_skill` or `update_skill`
- You just solved a non-obvious problem that will recur
- You followed an existing skill and found it wrong, vague or incomplete
- You researched an unfamiliar tool and want to keep what you learned

Do **not** write a skill for a one-off edit, a project-specific fact (put those in
`AGENTS.md`), or something you only half-understand — an unverified skill is worse
than none, because it will be trusted later.

## Procedure

1. Decide the scope. One skill = one class of task. "Deploy to Fly.io" is a skill;
   "Deploy" is too broad; "Run `fly deploy` in this repo" is too narrow.
2. Pick a kebab-case name that names the *task*, not the tool: `postgres-backup`,
   not `pg_dump`.
3. Draft the body with these sections:
   - `# Title`
   - `## Purpose` — the class of problem, in one or two sentences
   - `## When to use` — concrete triggers, as a bullet list
   - `## Procedure` — numbered steps with **exact** commands and file names
   - `## Common failures` — the real errors and what they mean
   - `## Verification` — how to prove it actually worked
   - `## Examples` — one worked example end to end
4. Replace every vague instruction with a concrete one. "Configure the server"
   is useless; "set `listen_addresses = '*'` in `postgresql.conf`" is a skill.
5. Include the commands you actually ran and their real output shape, not
   invented output.
6. Call `create_skill` with `scope="project"` for codebase-specific procedures
   and `scope="global"` for things true everywhere.
7. Immediately apply the skill to the task that prompted it. If following your
   own skill is awkward, the skill is wrong — fix it now with `update_skill`.

## Common failures

- **A diary, not a procedure.** "I read config.py, then edited line 40." Nobody
  can reuse that. Write what to do, not what you did.
- **Vague verbs.** "Handle errors appropriately", "set up the environment".
  State the exact action.
- **No verification section.** Then the next agent will claim success without
  checking, which is the failure mode skills exist to prevent.
- **Unverified claims.** Do not write a procedure you have not executed at least
  once, unless you mark the uncertain parts explicitly.
- **Duplicating an existing skill.** Always `list_skills` first; prefer
  `update_skill` over creating a near-duplicate.

## Verification

After `create_skill` returns:
1. Confirm the result says the skill is loaded and active.
2. Run `list_skills` — the new name must appear.
3. Follow your own procedure for the current task. If a step is ambiguous when
   you try to execute it, rewrite that step.

## Examples

Task: the user asks to add a pre-commit hook and you have never configured one
in this project.

1. `list_skills query="pre-commit hook git"` → nothing relevant.
2. Investigate: `read_file .pre-commit-config.yaml` (missing), `which pre-commit`.
3. Research the current syntax if unsure (`web_search`, `fetch_url`).
4. Implement and verify it works: `pre-commit run --all-files`.
5. `create_skill` named `pre-commit-setup` with the exact config file contents,
   the install command, the failure you hit (`pre-commit: command not found` →
   `pip install pre-commit`), and the verification command.
6. Continue the user's task using the procedure you just recorded.
