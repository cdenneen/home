{
  lib,
  python3,
  sqlite,
  curl,
  fzf,
}:
let
  pythonEnv = python3.withPackages (ps: [
    ps.httpx
    ps.aiohttp
  ]);
in
python3.pkgs.buildPythonApplication {
  pname = "opencode-telegram-bridge";
  version = "0.1.0";

  format = "other";
  dontBuild = true;

  src = lib.cleanSource ../.;

  propagatedBuildInputs = [ ];

  installPhase = ''
    runHook preInstall
    mkdir -p $out/bin $out/lib/opencode-telegram-bridge
    cp pkgs/opencode-telegram-bridge/bridge.py \
      $out/lib/opencode-telegram-bridge/bridge.py
    cat > $out/bin/opencode-telegram-bridge <<EOF
    #!/bin/sh
    exec ${pythonEnv}/bin/python $out/lib/opencode-telegram-bridge/bridge.py "$@"
    EOF
    cat > $out/bin/opencode-chat <<'EOF'
    #!/bin/sh
    set -euo pipefail

    db="$HOME/.local/share/opencode-telegram-bridge/state.sqlite"
    if [ ! -r "$db" ]; then
      echo "opencode-chat: bridge DB not found: $db" >&2
      exit 1
    fi

    pick=0
    watch=0
    reconnect=0
    for arg in "$@"; do
      case "$arg" in
        --pick) pick=1 ;;
        --watch) watch=1 ;;
        --reconnect) reconnect=1 ;;
      esac
    done

    if [ "$pick" -eq 1 ]; then
      row="$(${sqlite}/bin/sqlite3 "$db" "select chat_id, thread_id, workspace, opencode_port, opencode_session_id from topics where workspace is not null order by updated_at desc;" | ${fzf}/bin/fzf --prompt="opencode-chat> " --with-nth=1,2,3 --delimiter='|')"
    else
      row="$(${sqlite}/bin/sqlite3 "$db" "select chat_id, thread_id, workspace, opencode_port, opencode_session_id from topics where workspace is not null and opencode_session_id is not null order by updated_at desc limit 1;")"
    fi
    if [ -z "$row" ]; then
      echo "opencode-chat: no active sessions found" >&2
      exit 1
    fi

    IFS='|' read -r chat_id thread_id workspace port session_id <<EOF2
    $row
    EOF2

    if [ -z "$workspace" ] || [ -z "$session_id" ]; then
      echo "opencode-chat: missing port/session" >&2
      exit 1
    fi

    if [ -z "$port" ]; then
      port=0
    fi

    if ! ${curl}/bin/curl -sS --max-time 1 "http://127.0.0.1:''${port}/global/health" >/dev/null 2>&1; then
      log_dir="$HOME/.local/share/opencode-telegram-bridge"
      mkdir -p "$log_dir"
      log_file="$log_dir/opencode-serve.log"
      nohup opencode serve --hostname 127.0.0.1 --port "''${port}" >/dev/null 2>>"$log_file" &
      for _ in $(seq 1 20); do
        if ${curl}/bin/curl -sS --max-time 1 "http://127.0.0.1:''${port}/global/health" >/dev/null 2>&1; then
          break
        fi
        sleep 0.25
      done
    fi

    if [ "''${port}" = "0" ]; then
      port="$(${sqlite}/bin/sqlite3 "$db" "select opencode_port from topics where chat_id=''${chat_id} and thread_id=''${thread_id} limit 1;")"
    fi

    ${sqlite}/bin/sqlite3 "$db" "update topics set updated_at=strftime('%s','now') where chat_id=''${chat_id} and thread_id=''${thread_id};"

    if [ "$watch" -eq 1 ]; then
      (
        last=$(${sqlite}/bin/sqlite3 "$db" "select updated_at from topics where chat_id=''${chat_id} and thread_id=''${thread_id} limit 1;")
        while true; do
          sleep 15
          cur=$(${sqlite}/bin/sqlite3 "$db" "select updated_at from topics where chat_id=''${chat_id} and thread_id=''${thread_id} limit 1;")
          if [ "$cur" = "$last" ]; then
            echo "opencode-chat: no new updates; reattach if needed" >&2
            if [ "$reconnect" -eq 1 ]; then
              pkill -f "opencode attach http://127.0.0.1:''${port}" >/dev/null 2>&1 || true
              opencode attach "http://127.0.0.1:''${port}" --session "''${session_id}"
            fi
          else
            last="$cur"
          fi
        done
      ) &
    fi

    exec opencode attach "http://127.0.0.1:''${port}" --session "''${session_id}"
    EOF
    chmod +x $out/bin/opencode-telegram-bridge
    chmod +x $out/bin/opencode-chat
    runHook postInstall
  '';
}
