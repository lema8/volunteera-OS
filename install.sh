#!/usr/bin/env bash
#
# Install Lema Harness so that `lema` works from any directory.
#
#   ./install.sh              # install into ./.venv and link into ~/.local/bin
#   ./install.sh --uninstall  # remove the link
#   ./install.sh --prefix DIR # link somewhere else (e.g. /usr/local/bin)
#
# Why a link rather than `pip install --user`: Lema is a tool, not a library.
# Keeping it in its own virtualenv means its dependencies can never collide
# with a project you point it at, while the symlink keeps it on your PATH.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"
PREFIX="${HOME}/.local/bin"
UNINSTALL=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --uninstall) UNINSTALL=1; shift ;;
        --prefix) PREFIX="$2"; shift 2 ;;
        -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
    esac
done

say()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m  !\033[0m %s\n' "$*"; }
die()  { printf '\033[31m  ✗\033[0m %s\n' "$*" >&2; exit 1; }

if [[ $UNINSTALL -eq 1 ]]; then
    if [[ -L "$PREFIX/lema" ]]; then
        rm "$PREFIX/lema"
        ok "removed $PREFIX/lema"
    else
        warn "no link at $PREFIX/lema"
    fi
    say "the virtualenv at $VENV_DIR was left in place; delete it to remove Lema entirely"
    exit 0
fi

# --------------------------------------------------------------- interpreter

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    fi
done
[[ -n "$PYTHON" ]] || die "no Python 3.11+ found (install python3 and retry)"
say "using $($PYTHON --version) at $(command -v "$PYTHON")"

# ------------------------------------------------------------------- install

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    say "creating virtualenv at $VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR" || die "could not create a virtualenv (is python3-venv installed?)"
fi

say "installing Lema and its dependencies"
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip >/dev/null
"$VENV_DIR/bin/python" -m pip install --quiet -e "$REPO_DIR" || die "pip install failed"
[[ -x "$VENV_DIR/bin/lema" ]] || die "the lema entry point was not created"
ok "installed $("$VENV_DIR/bin/lema" --version)"

# ---------------------------------------------------------------------- link

mkdir -p "$PREFIX"
if [[ -e "$PREFIX/lema" && ! -L "$PREFIX/lema" ]]; then
    die "$PREFIX/lema exists and is not a symlink; remove it first"
fi
ln -sf "$VENV_DIR/bin/lema" "$PREFIX/lema"
ok "linked $PREFIX/lema -> $VENV_DIR/bin/lema"

# ---------------------------------------------------------------------- PATH

case ":$PATH:" in
    *":$PREFIX:"*)
        ok "$PREFIX is on your PATH"
        ;;
    *)
        warn "$PREFIX is NOT on your PATH"
        shell_name="$(basename "${SHELL:-bash}")"
        case "$shell_name" in
            fish) rc="~/.config/fish/config.fish"; line="fish_add_path $PREFIX" ;;
            zsh)  rc="~/.zshrc";                   line="export PATH=\"$PREFIX:\$PATH\"" ;;
            *)    rc="~/.bashrc";                  line="export PATH=\"$PREFIX:\$PATH\"" ;;
        esac
        echo
        echo "    Add this to $rc, then open a new shell:"
        echo
        echo "        $line"
        echo
        ;;
esac

echo
say "next steps"
echo "    lema doctor            check your environment and model endpoint"
echo "    lema init              write ~/.config/lema/config.toml"
echo "    lema                   start an interactive session"
echo "    lema \"fix the bug\"     run one task and exit"
