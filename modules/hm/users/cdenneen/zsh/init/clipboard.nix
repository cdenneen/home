''
    # Clipboard helpers (pbcopy/pbpaste over SSH, WSL, or OSC52)
    unalias pbcopy 2>/dev/null || true
    unalias pbpaste 2>/dev/null || true

  _pbcopy_osc52() {
    local data b64 osc
    data=$(cat)

    command -v base64 >/dev/null 2>&1 || return 1

      # base64 without line wraps (GNU coreutils uses -w0)
      b64=$(printf %s "$data" | base64 -w0 2>/dev/null || printf %s "$data" | base64 | tr -d '\n')
      osc="\033]52;c;''${b64}\a"

      # tmux needs DCS passthrough
      if [[ -n "$TMUX" ]]; then
        printf "\033Ptmux;''${osc}\033\\"
      else
        printf "%b" "$osc"
      fi
    }

    _is_wsl() {
      [[ -n "$WSL_DISTRO_NAME" || -n "$WSL_INTEROP" ]] && return 0

      [[ -r /proc/sys/kernel/osrelease ]] && grep -qiE '(microsoft|wsl)' /proc/sys/kernel/osrelease && return 0
      [[ -r /proc/version ]] && grep -qiE '(microsoft|wsl)' /proc/version && return 0

      return 1
    }

    _pbcopy_wsl() {
      command -v clip.exe >/dev/null 2>&1 || return 1
      clip.exe
    }

    _pbpaste_wsl() {
      command -v powershell.exe >/dev/null 2>&1 || return 1
      powershell.exe -NoProfile -Command "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Clipboard -Raw" | tr -d '\r'
    }

    _TCP_CLIPBOARD_OK=0
    _TCP_CLIPBOARD_TS=0

    _tcp_clipboard_available() {
      local now=''${EPOCHSECONDS:-0}
      if (( now - _TCP_CLIPBOARD_TS < 2 )); then
        return $_TCP_CLIPBOARD_OK
      fi

      _TCP_CLIPBOARD_TS=$now
      command -v nc >/dev/null 2>&1 || { _TCP_CLIPBOARD_OK=1; return 1; }

      nc -z -w 1 127.0.0.1 2491 >/dev/null 2>&1 || { _TCP_CLIPBOARD_OK=1; return 1; }
      nc -z -w 1 127.0.0.1 2492 >/dev/null 2>&1 || { _TCP_CLIPBOARD_OK=1; return 1; }

      _TCP_CLIPBOARD_OK=0
      return 0
    }

    _pbcopy_tcp() {
      command -v nc >/dev/null 2>&1 || return 1

      # OpenBSD netcat: close immediately after stdin EOF.
      nc -q 0 -w 5 127.0.0.1 2491 >/dev/null 2>&1
    }

    _pbpaste_tcp() {
      command -v nc >/dev/null 2>&1 || return 1

      # Avoid hanging forever if the bridge stalls.
      nc -w 5 127.0.0.1 2492
    }

    pbcopy() {
      if [[ -n "$SSH_CONNECTION" || -n "$SSH_TTY" ]]; then
        local tmp
        tmp=$(mktemp)

        cat >"$tmp"

        if _tcp_clipboard_available; then
          if cat "$tmp" | _pbcopy_tcp; then
            rm -f "$tmp"
            return
          fi
        fi

        if command -v lemonade >/dev/null 2>&1; then
          if cat "$tmp" | lemonade copy 2>/dev/null; then
            rm -f "$tmp"
            return
          fi
        fi

      if ! cat "$tmp" | _pbcopy_osc52; then
        rm -f "$tmp"
        return 1
      fi
      rm -f "$tmp"
      return
      fi

      if [[ "$OSTYPE" == darwin* ]] && command -v /usr/bin/pbcopy >/dev/null 2>&1; then
        /usr/bin/pbcopy
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

    pbpaste() {
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

      if [[ "$OSTYPE" == darwin* ]] && command -v /usr/bin/pbpaste >/dev/null 2>&1; then
        /usr/bin/pbpaste
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
''
