{
  config,
  homeStateVersion ? "25.11",
  lib,
  options,
  pkgs,
  ...
}:
let
  cfg = config.programs.ssh;
  hasSshAgentService = homeStateVersion != "25.05" && options.services ? ssh-agent;
  needsShellAgent = !hasSshAgentService && !pkgs.stdenv.hostPlatform.isDarwin;
  shellInit = ''
    # Use ssh-agent; only start one if there is no usable socket.
    if { [ -z "$SSH_AUTH_SOCK" ] || [ ! -S "$SSH_AUTH_SOCK" ]; }; then
      eval "$(ssh-agent -s)" >/dev/null
    fi
  '';
in
{
  config = lib.mkIf cfg.enable {
    home.file.".ssh/known_hosts.d/git-hosts".text = ''
      github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
      github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=
      github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=
      gitlab.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAfuCHKVTjquxvt6CM6tdG4SLp1Btn/nOeHHE5UOzRdf
      gitlab.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBFSMqzJeV9rUzU4kWitGjeR4PWSa29SPqJ1fVkhtj3Hw9xjLVXVYrU9QlYWrOLXBpQ6KWjbjTDTdDkoohFzgbEY=
      gitlab.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCsj2bNKTBSpIYDEGk9KxsGh3mySTRgMtXL583qmBpzeQ+jqCMRgBqB98u3z++J1sKlXHWfM9dyhSevkMwSbhoR8XIq/U0tCNyokEi/ueaBMCvbcTHhO7FcwzY92WK4Yt0aGROY5qX2UKSeOvuP4D6TPqKF1onrSzH9bx9XUf2lEdWT/ia1NEKjunUqu1xOB/StKDHMoX4/OKyIzuS0q/T1zOATthvasJFoPrAjkohTyaDUz2LN5JoH839hViyEG82yB+MjcFV5MU3N1l1QL3cVUCh93xSaua1N85qivl+siMkPGbO5xR/En4iEY6K2XPASUEMaieWVNTRCtJ4S8H+9
    '';
    home.file.".ssh/known_hosts.d/internal-hosts".text = ''
      nyx,nyx.tail0e55.ts.net,100.80.58.4 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK3PCrjUkoqJkZ1Ibi+s702ub7zrqvh44pxVFii5C/FG
      ghost,ghost.tail0e55.ts.net,100.114.242.29,150.136.97.147 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIr7jR0S7KbVD7+wYAqgCEiVVyUYhM2K90EiVKz7ofCd
      savage,100.76.222.17 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKhv+cYoSk3vklIXT6JiN2KydQ0Yqc6G2dM7ns5QcBtH
      flash,100.117.228.112,34.171.198.11 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDfbHp32yNF080ljwAUsImHCx/78fnTndM1GST7DIG6s
      onyx,100.67.24.27,150.136.243.163 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIC115wfVAmr673dTdsA8atoz/SCaP/H05S2O3+6XUMdZ
      talon,100.107.59.106,157.151.247.217 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEtLq0PsflmGDtlrAEagKaiD4kCzuX6PfKMlLNABDcrO
    '';
    services = lib.optionalAttrs hasSshAgentService {
      ssh-agent.enable = true;
    };
    programs =
      lib.optionalAttrs needsShellAgent {
        zsh.initContent = lib.mkAfter shellInit;
        bash.initExtra = lib.mkAfter shellInit;
      }
      // {
        ssh =
          if options.programs.ssh ? enableDefaultConfig then
            {
              enableDefaultConfig = false;
              settings."*" = {
                ForwardAgent = false;
                AddKeysToAgent = "no";
                Compression = false;
                ServerAliveInterval = 0;
                ServerAliveCountMax = 3;
                HashKnownHosts = false;
                UserKnownHostsFile = "~/.ssh/known_hosts ~/.ssh/known_hosts.d/git-hosts ~/.ssh/known_hosts.d/internal-hosts";
                ControlMaster = "no";
                ControlPath = "~/.ssh/master-%r@%n:%p";
                ControlPersist = "no";
              };
              # GCE ephemeral IPs -- update hostname here once each host's
              # tailscale identity is authenticated and stable.
              settings.savage = {
                HostName = "136.117.81.52";
                User = "cdenneen";
                IdentityFile = "~/.ssh/cdenneen_ed25519_2024";
              };
              settings.flash = {
                HostName = "34.171.198.11";
                User = "cdenneen";
                IdentityFile = "~/.ssh/cdenneen_ed25519_2024";
              };
            }
          else
            {
              settings."*" = {
                ForwardAgent = false;
                Compression = false;
                ServerAliveInterval = 0;
                ServerAliveCountMax = 3;
                HashKnownHosts = false;
                UserKnownHostsFile = "~/.ssh/known_hosts ~/.ssh/known_hosts.d/git-hosts ~/.ssh/known_hosts.d/internal-hosts";
              }
              // lib.optionalAttrs pkgs.stdenv.hostPlatform.isDarwin {
                AddKeysToAgent = "yes";
                UseKeychain = true;
              };
              # GCE ephemeral IPs -- update hostname here once each host's
              # tailscale identity is authenticated and stable.
              settings.savage = {
                HostName = "136.117.81.52";
                User = "cdenneen";
                IdentityFile = "~/.ssh/cdenneen_ed25519_2024";
              };
              settings.flash = {
                HostName = "34.171.198.11";
                User = "cdenneen";
                IdentityFile = "~/.ssh/cdenneen_ed25519_2024";
              };
            };
      };
  };
}
