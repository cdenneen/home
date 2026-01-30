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

function pbcopy() {
  if [[ -n "$SSH_CONNECTION" || -n "$SSH_TTY" ]]; then
    if command -v lemonade >/dev/null 2>&1; then
      lemonade copy
      return
    fi

    _pbcopy_osc52
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
      lemonade paste
      return
    fi

    # Fallback: ask user to paste (works everywhere, but manual)
    printf "Paste now, then press Ctrl-D\n" >&2
    cat
    return
  fi

  if command -v xsel >/dev/null 2>&1; then
    xsel -ob
    return
  fi

  return 1
}
