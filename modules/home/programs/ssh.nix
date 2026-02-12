{
  config,
  lib,
  ...
}:
let
  cfg = config.programs.ssh;

  shellInit = ''
    gpg_ssh_ok=0

    # Prefer gpg-agent as the SSH agent when available.
    if command -v gpgconf >/dev/null 2>&1; then
      gpgconf --launch gpg-agent >/dev/null 2>&1 || true
      gpg_ssh_sock="$(gpgconf --list-dirs agent-ssh-socket 2>/dev/null || true)"
      if [ -n "$gpg_ssh_sock" ] && [ -S "$gpg_ssh_sock" ]; then
        export SSH_AUTH_SOCK="$gpg_ssh_sock"
        gpg_ssh_ok=1

        # If the socket exists but isn't actually usable (connection refused),
        # recover the systemd user sockets.
        if command -v ssh-add >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1; then
          ssh_add_out="$(SSH_AUTH_SOCK="$gpg_ssh_sock" ssh-add -l 2>&1)" || ssh_add_rc=$?
          if [ "''${ssh_add_rc:-0}" -ne 0 ]; then
            case "$ssh_add_out" in
              *"Error connecting to agent"*|*"Connection refused"*)
                systemctl --user stop gpg-agent.service >/dev/null 2>&1 || true
                systemctl --user restart gpg-agent.socket gpg-agent-ssh.socket >/dev/null 2>&1 || true
                ;;
            esac
          fi
        fi
      fi
    fi

    # Fallback to a per-shell ssh-agent only when we don't already have one.
    if [ "$gpg_ssh_ok" -ne 1 ] && { [ -z "$SSH_AUTH_SOCK" ] || [ ! -S "$SSH_AUTH_SOCK" ]; }; then
      case "$SSH_AUTH_SOCK" in
        */ssh-*/agent.*) : ;;
        *) eval "$(ssh-agent -s)" >/dev/null ;;
      esac
    fi
  '';
in
{
  config = lib.mkIf cfg.enable {
    programs = {
      # Run late so we can override any earlier SSH_AUTH_SOCK exports.
      zsh.initContent = lib.mkAfter shellInit;
      bash.initExtra = lib.mkAfter shellInit;
      ssh = {
        enableDefaultConfig = false;
        matchBlocks."*" = {
          forwardAgent = false;
          addKeysToAgent = "no";
          compression = false;
          serverAliveInterval = 0;
          serverAliveCountMax = 3;
          hashKnownHosts = false;
          userKnownHostsFile = "~/.ssh/known_hosts";
          controlMaster = "no";
          controlPath = "~/.ssh/master-%r@%n:%p";
          controlPersist = "no";
        };
      };
    };
  };
}
