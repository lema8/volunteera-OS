---
name: linux-shell
description: Run shell commands reliably on Linux, including Ubuntu, Arch and Omarchy
keywords: linux, shell, bash, ubuntu, arch, omarchy, pacman, apt, systemd, permissions, path, process, port
version: 1
---

# Linux Shell

## Purpose

Execute shell work correctly on Linux, accounting for distribution differences
and avoiding the interactive prompts that hang an agent.

## When to use

Use this skill when installing packages, managing services, inspecting the
system, or when a command behaves differently from what you expected.

## Procedure

### Always

1. Check a tool exists before relying on it: `which` tool, or
   `run_command "command -v <tool>"`. Exit 127 means "not found".
2. Keep commands non-interactive. An agent cannot answer a prompt:
   - `apt-get -y`, `pacman --noconfirm`, `pip --no-input`
   - `DEBIAN_FRONTEND=noninteractive` is already set for you
3. Quote paths that may contain spaces.
4. Prefer relative paths inside the project; absolute paths elsewhere.

### Identify the distribution before installing anything

```bash
cat /etc/os-release
```

| Distro                | Install command                    |
|-----------------------|------------------------------------|
| Ubuntu / Debian       | `sudo apt-get install -y <pkg>`    |
| Arch / Omarchy / Manjaro | `sudo pacman -S --noconfirm <pkg>` |
| Fedora / RHEL         | `sudo dnf install -y <pkg>`        |
| Alpine                | `sudo apk add <pkg>`               |
| Any (user-level)      | `mise`, `asdf`, `nix`, language package managers |

Omarchy is Arch-based: use `pacman`, and `yay`/`paru` for the AUR.

Prefer a language-level package manager (`pip`, `npm`, `cargo`, `go install`)
over a system one when the dependency is a project dependency — it does not
need root and does not affect the user's system.

### Long-running commands

- A server or watcher must use `start_process`, not `run_command` — `run_command`
  waits for exit and will hit its timeout.
- Read its output with `get_process_output`, optionally with `wait_for` to block
  until "listening on" appears.
- Always `stop_process` when finished.

### Ports and processes

```bash
ss -ltnp                 # listening TCP sockets (replaces netstat)
ss -ltnp | grep :3000    # what holds port 3000
pgrep -af node           # matching processes with their command lines
kill -TERM <pid>         # then kill -KILL only if it ignores TERM
```

### Services (systemd)

```bash
systemctl status <unit>
journalctl -u <unit> -n 50 --no-pager
systemctl --user status <unit>     # user services
```

Always pass `--no-pager` — otherwise the command blocks in a pager.

## Common failures

- **`command not found` (exit 127)** — not installed, or not on PATH. Check with
  `command -v`; on Arch the binary name often differs from the package name.
- **`Permission denied` (exit 126 or EACCES)** — check `ls -l`, ownership, and
  the mount. Do not reflexively reach for `sudo`.
- **sudo hangs** — it wants a password on a TTY you do not have. Avoid sudo, or
  ask the user to run that step.
- **A command hangs forever** — it is waiting for input or paging. Add `-y`,
  `--noconfirm`, `--no-pager`, or redirect stdin from `/dev/null`.
- **`Address already in use`** — find the holder with `ss -ltnp` and either stop
  it or use another port.
- **A pipeline "succeeds" despite a failure** — the exit code is the last
  command's. Use `set -o pipefail` when it matters.
- **PATH differs from your interactive shell** — agent shells are not login
  shells; `~/.bashrc` may not be sourced. Use full paths or export PATH
  explicitly.

## Verification

- Exit code 0.
- For an install: re-run `command -v <tool>` and `<tool> --version`.
- For a service: `systemctl status` reports `active (running)`.
- For a server: `curl -sS -o /dev/null -w '%{http_code}' http://localhost:PORT`
  returns the expected status.

## Examples

**Install ripgrep on an unknown distro**

```bash
command -v rg || { . /etc/os-release; case "$ID" in
  ubuntu|debian) sudo apt-get install -y ripgrep ;;
  arch|omarchy|manjaro) sudo pacman -S --noconfirm ripgrep ;;
  fedora) sudo dnf install -y ripgrep ;;
esac; }
rg --version
```

**Start a dev server and confirm it is up**

```
start_process command="npm run dev" wait_for_log="ready|listening|localhost"
run_command "curl -sS -o /dev/null -w '%{http_code}' http://localhost:3000"
stop_process process_id="npm-1"
```
