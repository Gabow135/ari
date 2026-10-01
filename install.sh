#!/usr/bin/env bash
# Ari source installer — Linux, macOS, WSL2.
#
#   curl -fsSL https://raw.githubusercontent.com/Gabow135/ari/main/install.sh | bash
#
# Bootstraps the prerequisites Ari needs (git, Python >=3.11, Node/npm, uv, the
# Claude Code CLI), checks out the source into ~/.ari, builds the virtualenv and
# drops an `ari` launcher on your PATH. It never replaces tools you already have.
#
# Tunables (all optional):
#   ARI_HOME     install dir            (default: ~/.ari)
#   ARI_REPO     git remote             (default: https://github.com/Gabow135/ari.git)
#   ARI_REF      tag/branch to install  (default: latest release, else main)
#   ARI_BIN_DIR  launcher dir on PATH   (default: ~/.local/bin)
set -euo pipefail

ARI_SLUG="Gabow135/ari"
ARI_HOME="${ARI_HOME:-$HOME/.ari}"
ARI_REPO="${ARI_REPO:-https://github.com/$ARI_SLUG.git}"
ARI_BIN_DIR="${ARI_BIN_DIR:-$HOME/.local/bin}"
VENV="$ARI_HOME/.venv"

# ── logging ────────────────────────────────────────────────────────────────
if [ -t 2 ]; then
  B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; X=$'\033[0m'
else
  B=''; G=''; Y=''; R=''; C=''; X=''
fi
info() { printf '%s==>%s %s\n' "$C$B" "$X" "$*" >&2; }
ok()   { printf '%s  ok%s %s\n' "$G" "$X" "$*" >&2; }
warn() { printf '%swarn%s %s\n' "$Y" "$X" "$*" >&2; }
die()  { printf '%serror%s %s\n' "$R$B" "$X" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# ── platform + package manager ─────────────────────────────────────────────
OS="$(uname -s)"
PM=""
SUDO=""
detect_pm() {
  case "$OS" in
    Darwin)
      have brew && PM="brew" || warn "Homebrew not found — some prerequisites may need manual install (https://brew.sh)."
      ;;
    Linux)
      if   have apt-get; then PM="apt"
      elif have dnf;     then PM="dnf"
      elif have pacman;  then PM="pacman"
      elif have zypper;  then PM="zypper"
      elif have apk;     then PM="apk"
      else warn "No supported package manager found — install prerequisites manually if a step fails."
      fi
      [ "$(id -u)" -ne 0 ] && have sudo && SUDO="sudo"
      ;;
    *) warn "Unrecognized platform '$OS' — continuing best-effort." ;;
  esac
}

# pm_install <brew-formula> <apt-pkg> <dnf-pkg> <pacman-pkg> <zypper-pkg> <apk-pkg>
pm_install() {
  case "$PM" in
    brew)   brew install "$1" ;;
    apt)    $SUDO apt-get update -qq && $SUDO apt-get install -y "$2" ;;
    dnf)    $SUDO dnf install -y "$3" ;;
    pacman) $SUDO pacman -Sy --noconfirm "$4" ;;
    zypper) $SUDO zypper install -y "$5" ;;
    apk)    $SUDO apk add "$6" ;;
    *)      return 1 ;;
  esac
}

# ── python >=3.11 discovery ────────────────────────────────────────────────
find_python() {
  local c v major minor
  for c in python3.13 python3.12 python3.11 python3; do
    have "$c" || continue
    v="$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)" || continue
    major="${v%.*}"; minor="${v#*.}"
    if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; then printf '%s' "$c"; return 0; fi
  done
  return 1
}

ensure_prereqs() {
  info "Checking prerequisites…"

  have git || { info "Installing git"; pm_install git git git git git git || die "Could not install git — install it and re-run."; }

  if ! PYTHON="$(find_python)"; then
    info "Installing Python 3.12"
    pm_install python@3.12 python3 python3 python python3 python3 || true
    # Debian/Ubuntu split venv out of the base package.
    [ "$PM" = "apt" ] && $SUDO apt-get install -y python3-venv >/dev/null 2>&1 || true
    PYTHON="$(find_python)" || die "Python >=3.11 is required but could not be installed automatically."
  fi
  ok "Python: $("$PYTHON" --version 2>&1)"

  if ! have npm; then
    info "Installing Node.js + npm"
    pm_install node nodejs nodejs nodejs nodejs18 nodejs || warn "Could not install Node automatically — npx-based MCP servers (mysql, filesystem, email) need it."
  fi
  have node && ok "Node: $(node --version 2>&1)"

  if ! have uv; then
    info "Installing uv (astral.sh)"
    curl -LsSf https://astral.sh/uv/install.sh | sh || die "uv install failed."
  fi
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  have uv || die "uv is not on PATH after install — open a new shell and re-run."
  ok "uv: $(uv --version 2>&1)"

  if ! have claude; then
    info "Installing the Claude Code CLI"
    if have npm; then
      npm install -g @anthropic-ai/claude-code >/dev/null 2>&1 \
        && ok "Claude Code CLI installed" \
        || warn "Could not install the Claude Code CLI globally. Install it manually: npm install -g @anthropic-ai/claude-code"
    else
      warn "npm unavailable — install the Claude Code CLI manually: npm install -g @anthropic-ai/claude-code"
    fi
  else
    ok "Claude Code CLI: present"
  fi
}

# ── source checkout ────────────────────────────────────────────────────────
resolve_ref() {
  if [ -n "${ARI_REF:-}" ]; then printf '%s' "$ARI_REF"; return; fi
  local tag
  tag="$(curl -fsSL "https://api.github.com/repos/$ARI_SLUG/releases/latest" 2>/dev/null \
        | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
  [ -n "$tag" ] && printf '%s' "$tag" || printf 'main'
}

fetch_source() {
  local ref; ref="$(resolve_ref)"
  if [ -d "$ARI_HOME/.git" ]; then
    info "Updating Ari in $ARI_HOME (ref: $ref)"
    git -C "$ARI_HOME" fetch --tags --quiet origin
    git -C "$ARI_HOME" checkout --quiet "$ref"
    git -C "$ARI_HOME" pull --quiet --ff-only origin "$ref" 2>/dev/null || true
  else
    info "Cloning Ari into $ARI_HOME (ref: $ref)"
    git clone --quiet "$ARI_REPO" "$ARI_HOME"
    git -C "$ARI_HOME" checkout --quiet "$ref"
  fi
  ok "Source at $(git -C "$ARI_HOME" rev-parse --short HEAD)"
}

# ── build + wire up ────────────────────────────────────────────────────────
build_venv() {
  info "Creating virtualenv and installing dependencies (this can take a minute)…"
  uv venv --python "$PYTHON" "$VENV" >/dev/null
  uv pip install --python "$VENV/bin/python" -e "$ARI_HOME" >/dev/null
  ok "Dependencies installed into $VENV"
}

install_launcher() {
  mkdir -p "$ARI_BIN_DIR"
  cat > "$ARI_BIN_DIR/ari" <<EOF
#!/usr/bin/env bash
# Ari launcher — runs the bot inside its own virtualenv.
exec "$VENV/bin/python" -m ari.main "\$@"
EOF
  chmod +x "$ARI_BIN_DIR/ari"
  ok "Launcher installed: $ARI_BIN_DIR/ari"
}

seed_env() {
  if [ ! -f "$ARI_HOME/.env" ] && [ -f "$ARI_HOME/.env.example" ]; then
    cp "$ARI_HOME/.env.example" "$ARI_HOME/.env"
    ok "Created $ARI_HOME/.env from the template"
  fi
}

main() {
  printf '%s\n' "${B}Ari installer${X} — Linux/macOS/WSL2" >&2
  detect_pm
  ensure_prereqs
  fetch_source
  build_venv
  install_launcher
  seed_env

  printf '\n%s%s Ari is installed.%s\n' "$G" "$B" "$X" >&2
  info "Next steps:"
  printf '  1. %sclaude login%s                 # authenticate the Claude Code CLI\n' "$B" "$X" >&2
  printf '  2. edit %s%s/.env%s             # Telegram token, owner id, credentials\n' "$B" "$ARI_HOME" "$X" >&2
  printf '  3. run %sari%s\n' "$B" "$X" >&2
  case ":$PATH:" in
    *":$ARI_BIN_DIR:"*) ;;
    *) printf '\n' >&2; warn "$ARI_BIN_DIR is not on your PATH. Add it:"
       printf '    export PATH="%s:$PATH"   # add to ~/.bashrc or ~/.zshrc\n' "$ARI_BIN_DIR" >&2 ;;
  esac
}

main "$@"
