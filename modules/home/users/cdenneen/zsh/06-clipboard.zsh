#█▓▒░ clipboard helpers
# Goal: `pbcopy`/`pbpaste` on a remote SSH session uses your local clipboard.
#
# Preferred: lemonade over SSH remote port forward.
# Fallback: OSC52 for remote -> local copy, and an interactive paste prompt.

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
  [[ -n "$WSL_DISTRO_NAME" || -n "$WSL_INTEROP" ]]
}

function _pbcopy_wsl() {
  if command -v clip.exe >/dev/null 2>&1; then
    clip.exe
    return
  fi

  cat >/dev/null
  return 1
}

function _pbpaste_wsl() {
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Clipboard -Raw" | tr -d '\r'
    return
  fi

  return 1
}

function _pbcopy_tcp() {
  if command -v nc >/dev/null 2>&1; then
    nc 127.0.0.1 2491 >/dev/null
    return $?
  fi

  cat >/dev/null
  return 1
}

function _pbpaste_tcp() {
  if command -v nc >/dev/null 2>&1; then
    nc 127.0.0.1 2492
    return $?
  fi

  return 1
}

function pbcopy() {
  if [[ -n "$SSH_CONNECTION" || -n "$SSH_TTY" ]]; then
    local tmp
    tmp=$(mktemp)
    trap 'rm -f "$tmp"' RETURN

    cat >"$tmp"

    if command -v lemonade >/dev/null 2>&1; then
      cat "$tmp" | lemonade copy 2>/dev/null && return
    fi

    cat "$tmp" | _pbcopy_tcp && return

    cat "$tmp" | _pbcopy_osc52
    return
  fi

  if _is_wsl; then
    _pbcopy_wsl
    return
  fi

  if command -v xsel >/dev/null 2>&1; then
    xsel -ib
    return
  fi

  cat >/dev/null
  return 1
}

function pbpaste() {
  if [[ -n "$SSH_CONNECTION" || -n "$SSH_TTY" ]]; then
    if command -v lemonade >/dev/null 2>&1; then
      lemonade paste 2>/dev/null && return
    fi

    _pbpaste_tcp && return

    # Fallback: ask user to paste (works everywhere, but manual)
    printf "Paste now, then press Ctrl-D\n" >&2
    cat
    return
  fi

  if _is_wsl; then
    _pbpaste_wsl
    return
  fi

  if command -v xsel >/dev/null 2>&1; then
    xsel -ob
    return
  fi

  return 1
}
