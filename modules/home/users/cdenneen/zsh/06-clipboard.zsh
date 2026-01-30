#█▓▒░ clipboard helpers
# Goal: `pbcopy`/`pbpaste` on a remote SSH session uses your local clipboard.
#
# Preferred (WSL): TCP bridge over SSH remote port forward.
# Preferred (macOS): lemonade over SSH remote port forward.
# Fallback: OSC52 for remote -> local copy, and an interactive paste prompt.

# Ensure we override any legacy aliases.
unalias pbcopy 2>/dev/null || true
unalias pbpaste 2>/dev/null || true

function _pbcopy_osc52() {
  local data b64 osc
  data=$(cat)

  # base64 without line wraps (GNU coreutils uses -w0)
  b64=$(printf %s "$data" | base64 -w0 2>/dev/null || printf %s "$data" | base64 | tr -d '\n')
  osc="\033]52;c;${b64}\a"

  # tmux needs DCS passthrough
  if [[ -n "$TMUX" ]]; then
    printf "\033Ptmux;${osc}\033\\"
  else
    printf "%b" "$osc"
  fi
}

function _is_wsl() {
  [[ -n "$WSL_DISTRO_NAME" || -n "$WSL_INTEROP" ]] && return 0

  [[ -r /proc/sys/kernel/osrelease ]] && grep -qiE '(microsoft|wsl)' /proc/sys/kernel/osrelease && return 0
  [[ -r /proc/version ]] && grep -qiE '(microsoft|wsl)' /proc/version && return 0

  return 1
}

function _pbcopy_wsl() {
  command -v clip.exe >/dev/null 2>&1 || return 1
  clip.exe
}

function _pbpaste_wsl() {
  command -v powershell.exe >/dev/null 2>&1 || return 1
  powershell.exe -NoProfile -Command "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Clipboard -Raw" | tr -d '\r'
}

function _tcp_clipboard_available() {
  command -v nc >/dev/null 2>&1 || return 1

  nc -z -w 1 127.0.0.1 2491 >/dev/null 2>&1 || return 1
  nc -z -w 1 127.0.0.1 2492 >/dev/null 2>&1 || return 1

  return 0
}

function _pbcopy_tcp() {
  command -v nc >/dev/null 2>&1 || return 1
  nc 127.0.0.1 2491 >/dev/null
}

function _pbpaste_tcp() {
  command -v nc >/dev/null 2>&1 || return 1
  nc 127.0.0.1 2492
}

function pbcopy() {
  if [[ -n "$SSH_CONNECTION" || -n "$SSH_TTY" ]]; then
    local tmp
    tmp=$(mktemp)
    trap 'rm -f "$tmp"' RETURN

    cat >"$tmp"

    if _tcp_clipboard_available; then
      cat "$tmp" | _pbcopy_tcp && return
    fi

    if command -v lemonade >/dev/null 2>&1; then
      cat "$tmp" | lemonade copy 2>/dev/null && return
    fi

    cat "$tmp" | _pbcopy_osc52
    return
  fi

  # Prefer Windows clipboard whenever available (WSL).
  if command -v clip.exe >/dev/null 2>&1 || _is_wsl; then
    _pbcopy_wsl && return
  fi

  if [[ -n "$DISPLAY" ]] && command -v xsel >/dev/null 2>&1; then
    xsel -ib
    return
  fi

  cat >/dev/null
  return 1
}

function pbpaste() {
  if [[ -n "$SSH_CONNECTION" || -n "$SSH_TTY" ]]; then
    if _tcp_clipboard_available; then
      _pbpaste_tcp && return
    fi

    if command -v lemonade >/dev/null 2>&1; then
      lemonade paste 2>/dev/null && return
    fi

    # Fallback: ask user to paste (works everywhere, but manual)
    printf "Paste now, then press Ctrl-D\n" >&2
    cat
    return
  fi

  # Prefer Windows clipboard whenever available (WSL).
  if command -v powershell.exe >/dev/null 2>&1 || _is_wsl; then
    _pbpaste_wsl && return
  fi

  if [[ -n "$DISPLAY" ]] && command -v xsel >/dev/null 2>&1; then
    xsel -ob
    return
  fi

  return 1
}
