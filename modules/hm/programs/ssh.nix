{
  config,
  lib,
  ...
}:
let
  cfg = config.programs.ssh;

  shellInit = ''
    # Use ssh-agent; only start one if we don't already have a usable socket.
    if { [ -z "$SSH_AUTH_SOCK" ] || [ ! -S "$SSH_AUTH_SOCK" ]; }; then
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
