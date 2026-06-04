{
  config,
  lib,
  pkgs,
  happier,
  ...
}:
let
  aiHost = "ai.denneen.net";
  aiApiPort = 8080;
  aiWsPort = 8081;
  aiAppPort = 3000;
  aiProxyPort = 18080;
  ghostTunnelId = "1481e71c-a53f-4fe0-8983-468a3e0fffdf";
  ghostCloudflareCredFile = "/var/lib/cloudflared/ghost.json";
  pepsApiHost = "peps-api.denneen.net";
  pepsWebHost = "peps.denneen.net";
  pepsRepoDir = "/var/lib/peps/repo";
  pepsRuntimeDir = "/var/lib/peps";
  pepsGitRemote = "chris@100.125.246.107:/volume1/Git/peptide_tracker.git";
  pepsGitBranch = "main";
  pepsApiPort = 8787;
  pepsHealthImportTokenFile = "${pepsRuntimeDir}/health_import_token";
  wellnessApiHost = "wellness-api.denneen.net";
  wellnessRuntimeDir = "/var/lib/wellness";
  wellnessRepoDir = "${wellnessRuntimeDir}/repo";
  wellnessGitRemote = "git@github.com:cdenneen/wellness-tracker.git";
  wellnessGitBranch = "main";
  wellnessApiPort = 8797;
  wellnessSupabaseUrl = "https://kefpmmjhtdxhhhcndrnx.supabase.co";
  openAiKeyFile = config.sops.secrets.openai_api_key.path;
  geminiKeyFile = config.sops.secrets.gemini_api_key.path;
  wellnessSupabasePublishableKeyFile = config.sops.secrets.wellness_supabase_publishable_key.path;
  wellnessSupabaseSecretKeyFile = config.sops.secrets.wellness_supabase_secret_key.path;
  wellnessSupabaseDbUrlFile = config.sops.secrets.wellness_supabase_db_url.path;
  supabaseAccessTokenFile = config.sops.secrets.supabase_access_token.path;
in
{
  imports = [
    ./ghost-base.nix
    happier.nixosModules.happier-server
  ];

  containerPresets = {
    podman.enable = true;
  };
  networking = {
    firewall.trustedInterfaces = lib.mkAfter [ "tailscale0" ];
  };

  users.users.cdenneen.extraGroups = lib.mkAfter [ "tailscale" ];

  environment.systemPackages = lib.mkAfter [
    pkgs.caddy
    pkgs.cloudflared
    pkgs.nodejs_24
  ];

  services = {
    tailscale = {
      enable = true;
      openFirewall = true;
    };

    happier-server = {
      enable = true;
      package = happier.packages.${pkgs.stdenv.hostPlatform.system}.happier-server;
      mode = "light";
      port = 3005;
      environmentFile = "/var/lib/happier-server/happier.env";
    };

    caddy = {
      enable = true;
      virtualHosts."http://127.0.0.1:${toString aiProxyPort}".extraConfig = ''
        handle /api {
          reverse_proxy 127.0.0.1:${toString aiApiPort}
        }
        handle /api/* {
          reverse_proxy 127.0.0.1:${toString aiApiPort}
        }
        handle /ws {
          reverse_proxy 127.0.0.1:${toString aiWsPort}
        }
        handle /ws/* {
          reverse_proxy 127.0.0.1:${toString aiWsPort}
        }
        handle /slack/events {
          reverse_proxy 127.0.0.1:${toString aiAppPort}
        }
        handle /slack/events/* {
          reverse_proxy 127.0.0.1:${toString aiAppPort}
        }
        handle {
          reverse_proxy 127.0.0.1:${toString aiAppPort}
        }
      '';
    };

    cloudflared = {
      enable = true;
      tunnels."${ghostTunnelId}" = {
        credentialsFile = ghostCloudflareCredFile;
        ingress = {
          "${aiHost}" = "http://127.0.0.1:${toString aiProxyPort}";
          "${pepsApiHost}" = "http://127.0.0.1:${toString pepsApiPort}";
          "${pepsWebHost}" = "http://127.0.0.1:${toString pepsApiPort}";
          "${wellnessApiHost}" = "http://127.0.0.1:${toString wellnessApiPort}";
        };
        default = "http_status:404";
        originRequest = {
          connectTimeout = "30s";
          noTLSVerify = false;
        };
      };
    };
  };

  sops.secrets.ghost_cloudflare_tunnel_token = {
    owner = "root";
    group = "root";
    mode = "0400";
  };
  sops.secrets.openai_api_key = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.gemini_api_key = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.wellness_supabase_publishable_key = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.wellness_supabase_secret_key = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.wellness_supabase_db_url = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.supabase_access_token = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/cloudflared 0700 root root -"
    "d /var/lib/happier-server 0700 root root -"
    "d ${pepsRuntimeDir} 0750 cdenneen users -"
    "d ${pepsRepoDir} 0750 cdenneen users -"
    "d ${wellnessRuntimeDir} 0750 cdenneen users -"
    "d ${wellnessRepoDir} 0750 cdenneen users -"
  ];

  systemd.services.happier-env-bootstrap = {
    description = "Bootstrap HANDY_MASTER_SECRET for happier-server";
    before = [ "happier-server.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [
      pkgs.coreutils
      pkgs.gnugrep
      pkgs.openssl
    ];
    script = ''
      set -euo pipefail

      env_file="/var/lib/happier-server/happier.env"
      ${pkgs.coreutils}/bin/mkdir -p /var/lib/happier-server

      if [ ! -s "$env_file" ] || ! ${pkgs.gnugrep}/bin/grep -q '^HANDY_MASTER_SECRET=' "$env_file"; then
        secret="$(${pkgs.openssl}/bin/openssl rand -base64 48 | ${pkgs.coreutils}/bin/tr -d '\n\r')"
        ${pkgs.coreutils}/bin/install -m 600 /dev/null "$env_file"
        printf 'HANDY_MASTER_SECRET=%s\n' "$secret" > "$env_file"
      fi

      ${pkgs.coreutils}/bin/chmod 600 "$env_file"
    '';
  };

  systemd.services.happier-server = {
    requires = [ "happier-env-bootstrap.service" ];
    after = [ "happier-env-bootstrap.service" ];
  };

  systemd.services.peps-sync = {
    description = "Sync peps repo from NAS over Tailscale";
    after = [
      "network-online.target"
      "tailscaled.service"
    ];
    wants = [
      "network-online.target"
      "tailscaled.service"
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = pepsRuntimeDir;
    };
    path = [
      pkgs.coreutils
      pkgs.git
      pkgs.openssh
    ];
    script = ''
      set -euo pipefail

      if [ ! -r /home/cdenneen/.ssh/id_ed25519 ]; then
        echo "Missing /home/cdenneen/.ssh/id_ed25519 for NAS clone auth" >&2
        exit 1
      fi

      export GIT_SSH_COMMAND="ssh -i /home/cdenneen/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

      if [ ! -d "${pepsRepoDir}/.git" ]; then
        rm -rf "${pepsRepoDir}"
        git clone --branch "${pepsGitBranch}" "${pepsGitRemote}" "${pepsRepoDir}"
      fi

      cd "${pepsRepoDir}"
      git remote set-url origin "${pepsGitRemote}"
      git fetch --prune origin "${pepsGitBranch}"
      git checkout -B "${pepsGitBranch}" "origin/${pepsGitBranch}"
      git reset --hard "origin/${pepsGitBranch}"
    '';
  };

  systemd.timers.peps-sync = {
    description = "Periodic peps repo sync";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2m";
      OnUnitActiveSec = "10m";
      Unit = "peps-sync.service";
    };
  };

  systemd.services.wellness-sync = {
    description = "Sync wellness repo from GitHub";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type = "oneshot";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = wellnessRuntimeDir;
    };
    path = [
      pkgs.coreutils
      pkgs.git
      pkgs.openssh
    ];
    script = ''
      set -euo pipefail

      if [ ! -r /home/cdenneen/.ssh/id_ed25519 ]; then
        echo "Missing /home/cdenneen/.ssh/id_ed25519 for wellness clone auth" >&2
        exit 1
      fi

      export GIT_SSH_COMMAND="ssh -i /home/cdenneen/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

      if [ ! -d "${wellnessRepoDir}/.git" ]; then
        rm -rf "${wellnessRepoDir}"
        git clone --branch "${wellnessGitBranch}" "${wellnessGitRemote}" "${wellnessRepoDir}"
      fi

      cd "${wellnessRepoDir}"
      git remote set-url origin "${wellnessGitRemote}"
      git fetch --prune origin "${wellnessGitBranch}"
      git checkout -B "${wellnessGitBranch}" "origin/${wellnessGitBranch}"
      git reset --hard "origin/${wellnessGitBranch}"
    '';
  };

  systemd.timers.wellness-sync = {
    description = "Periodic wellness repo sync";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "3m";
      OnUnitActiveSec = "10m";
      Unit = "wellness-sync.service";
    };
  };

  systemd.services.peps-api = {
    description = "Peps API/web runtime";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "tailscaled.service"
      "peps-sync.service"
    ];
    wants = [
      "network-online.target"
      "tailscaled.service"
    ];
    requires = [ "peps-sync.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.nodejs_24
    ];
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = pepsRepoDir;
      Restart = "always";
      RestartSec = "10s";
      TimeoutStartSec = "20min";
      EnvironmentFile = [ "${pepsRepoDir}/deploy/backend/.env" ];
      Environment = [
        "HOME=/home/cdenneen"
        "API_PORT=${toString pepsApiPort}"
        "AUTH_REQUIRED=true"
        "AUTH_ADMIN_EMAILS=cdenneen@gmail.com,c.denneen@gmail.com"
        "SUPABASE_URL=${wellnessSupabaseUrl}"
        "PEPS_STATE_PROVIDER=supabase"
        "PEPS_STATE_TABLE=peps_app_state"
        "PEPS_STATE_ROW_ID=global"
        "PEPS_STATE_FILE_PATH=/home/cdenneen/.local/state/peps-api/web-state.json"
      ];
    };
    script = ''
      set -euo pipefail

      if [ ! -f package.json ]; then
        echo "peps-api: repository not found at ${pepsRepoDir}" >&2
        exit 1
      fi

      export SUPABASE_URL="${wellnessSupabaseUrl}"

      if [ -r "${wellnessSupabasePublishableKeyFile}" ]; then
        supabase_publishable_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${wellnessSupabasePublishableKeyFile}")"
        if [ -n "$supabase_publishable_key" ]; then
          export SUPABASE_PUBLISHABLE_KEY="$supabase_publishable_key"
          export SUPABASE_ANON_KEY="$supabase_publishable_key"
          export NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY="$supabase_publishable_key"
        fi
      fi

      if [ -r "${geminiKeyFile}" ]; then
        gemini_api_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${geminiKeyFile}")"
        if [ -n "$gemini_api_key" ]; then
          export GEMINI_API_KEY="$gemini_api_key"
          export GOOGLE_API_KEY="$gemini_api_key"
        else
          echo "peps-api: Gemini key is empty in ${geminiKeyFile}" >&2
        fi
      else
        echo "peps-api: Gemini key file missing at ${geminiKeyFile}" >&2
      fi

      if [ -r "${wellnessSupabaseSecretKeyFile}" ]; then
        supabase_secret_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${wellnessSupabaseSecretKeyFile}")"
        if [ -n "$supabase_secret_key" ] && [ "$supabase_secret_key" != "REPLACE_WITH_SB_SECRET_KEY" ]; then
          export SUPABASE_SECRET_KEY="$supabase_secret_key"
          export SUPABASE_SERVICE_ROLE_KEY="$supabase_secret_key"
        else
          echo "peps-api: Supabase secret key is unset in ${wellnessSupabaseSecretKeyFile}; using file fallback state store" >&2
        fi
      fi

      export PEPS_STATE_PROVIDER="supabase"
      export PEPS_STATE_TABLE="peps_app_state"
      export PEPS_STATE_ROW_ID="global"
      export PEPS_STATE_FILE_PATH="/home/cdenneen/.local/state/peps-api/web-state.json"

      if [ -r "${pepsHealthImportTokenFile}" ]; then
        export HEALTH_IMPORT_TOKEN="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${pepsHealthImportTokenFile}")"
      fi

      if [ ! -x node_modules/.bin/tsx ]; then
        npm ci --include=dev --no-audit --no-fund
      fi

      npm run web:build
      exec npm run api:start
    '';
  };

  systemd.services.wellness-api = {
    description = "Wellness Tracker API";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "wellness-sync.service"
    ];
    wants = [
      "network-online.target"
      "wellness-sync.service"
    ];
    requires = [ "wellness-sync.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.nodejs_24
    ];
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = wellnessRepoDir;
      Restart = "always";
      RestartSec = "10s";
      TimeoutStartSec = "15min";
      Environment = [
        "HOME=/home/cdenneen"
        "EXPO_PUBLIC_API_BASE_URL=https://${wellnessApiHost}"
        "EXPO_PUBLIC_SUPABASE_URL=${wellnessSupabaseUrl}"
        "API_BIND_HOST=127.0.0.1"
        "API_PORT=${toString wellnessApiPort}"
        "CORS_ALLOW_ORIGINS=*"
        "SUPABASE_URL=${wellnessSupabaseUrl}"
        "ENCRYPTED_STATE_TABLE=wellness_encrypted_state"
        "ENCRYPTED_STATE_FILE_PATH=/home/cdenneen/.local/state/wellness-api/encrypted-state.json"
        "AI_MODEL=gemini-3.5-flash"
      ];
    };
    script = ''
      set -euo pipefail

      if [ -r "${openAiKeyFile}" ]; then
        export OPENAI_API_KEY="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${openAiKeyFile}")"
      else
        echo "wellness-api: OpenAI key file missing at ${openAiKeyFile}" >&2
      fi

      if [ -r "${geminiKeyFile}" ]; then
        gemini_api_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${geminiKeyFile}")"
        if [ -n "$gemini_api_key" ]; then
          export GEMINI_API_KEY="$gemini_api_key"
          export GOOGLE_API_KEY="$gemini_api_key"
        else
          echo "wellness-api: Gemini key is empty in ${geminiKeyFile}" >&2
        fi
      else
        echo "wellness-api: Gemini key file missing at ${geminiKeyFile}" >&2
      fi

      if [ ! -r "${wellnessSupabasePublishableKeyFile}" ]; then
        echo "wellness-api: Supabase publishable key file missing at ${wellnessSupabasePublishableKeyFile}" >&2
        exit 1
      fi

      supabase_publishable_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${wellnessSupabasePublishableKeyFile}")"
      if [ -z "$supabase_publishable_key" ]; then
        echo "wellness-api: Supabase publishable key is empty" >&2
        exit 1
      fi
      export EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY="$supabase_publishable_key"
      export EXPO_PUBLIC_SUPABASE_ANON_KEY="$supabase_publishable_key"
      export SUPABASE_PUBLISHABLE_KEY="$supabase_publishable_key"
      export SUPABASE_ANON_KEY="$supabase_publishable_key"

      if [ -r "${wellnessSupabaseSecretKeyFile}" ]; then
        supabase_secret_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${wellnessSupabaseSecretKeyFile}")"
        if [ -n "$supabase_secret_key" ] && [ "$supabase_secret_key" != "REPLACE_WITH_SB_SECRET_KEY" ]; then
          export SUPABASE_SECRET_KEY="$supabase_secret_key"
          export SUPABASE_SERVICE_ROLE_KEY="$supabase_secret_key"
        else
          echo "wellness-api: Supabase secret key is unset in ${wellnessSupabaseSecretKeyFile}" >&2
        fi
      else
        echo "wellness-api: Supabase secret key file missing at ${wellnessSupabaseSecretKeyFile} (account deletion will be limited)" >&2
      fi

      if [ -r "${wellnessSupabaseDbUrlFile}" ]; then
        supabase_db_url="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${wellnessSupabaseDbUrlFile}")"
        if [ -n "$supabase_db_url" ] && [ "$supabase_db_url" != "REPLACE_WITH_SUPABASE_DB_URL" ]; then
          export SUPABASE_DB_URL="$supabase_db_url"
        else
          echo "wellness-api: Supabase DB URL is unset in ${wellnessSupabaseDbUrlFile}" >&2
        fi
      else
        echo "wellness-api: Supabase DB URL file missing at ${wellnessSupabaseDbUrlFile} (deploy migrations will require manual DB URL)" >&2
      fi

      if [ -r "${supabaseAccessTokenFile}" ]; then
        supabase_access_token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${supabaseAccessTokenFile}")"
        if [ -n "$supabase_access_token" ] && [ "$supabase_access_token" != "REPLACE_WITH_SUPABASE_ACCESS_TOKEN" ]; then
          export SUPABASE_ACCESS_TOKEN="$supabase_access_token"
        fi
      fi

      if [ ! -x node_modules/.bin/tsx ]; then
        npm ci --include=dev --no-audit --no-fund
      fi

      exec npm run api:start
    '';
  };

  systemd.services.cloudflared-credentials-ghost =
    let
      script = pkgs.writeShellScript "cloudflared-credentials-ghost" ''
        set -euo pipefail

        token_file="${config.sops.secrets.ghost_cloudflare_tunnel_token.path}"
        cred_dir="/var/lib/cloudflared"
        cred_file="${ghostCloudflareCredFile}"

        if [ ! -r "$token_file" ]; then
          echo "cloudflared-credentials-ghost: token file not readable" >&2
          exit 1
        fi

        token_json="$(${pkgs.coreutils}/bin/cat "$token_file" | ${pkgs.coreutils}/bin/tr -d '\n\r' | ${pkgs.coreutils}/bin/base64 -d)"
        account_tag="$(${pkgs.jq}/bin/jq -r '.a // empty' <<<"$token_json")"
        tunnel_id="$(${pkgs.jq}/bin/jq -r '.t // empty' <<<"$token_json")"
        tunnel_secret="$(${pkgs.jq}/bin/jq -r '.s // empty' <<<"$token_json")"

        if [ -z "$account_tag" ] || [ -z "$tunnel_id" ] || [ -z "$tunnel_secret" ]; then
          echo "cloudflared-credentials-ghost: invalid token contents" >&2
          exit 1
        fi

        if [ "$tunnel_id" != "${ghostTunnelId}" ]; then
          echo "cloudflared-credentials-ghost: token tunnel ID $tunnel_id does not match ${ghostTunnelId}" >&2
          exit 1
        fi

        ${pkgs.coreutils}/bin/mkdir -p "$cred_dir"
        ${pkgs.jq}/bin/jq -n \
          --arg account_tag "$account_tag" \
          --arg tunnel_id "$tunnel_id" \
          --arg tunnel_secret "$tunnel_secret" \
          --arg tunnel_name "ghost" \
          '{
            AccountTag: $account_tag,
            TunnelID: $tunnel_id,
            TunnelName: $tunnel_name,
            TunnelSecret: $tunnel_secret
          }' >"$cred_file"
        ${pkgs.coreutils}/bin/chmod 0400 "$cred_file"
      '';
    in
    {
      description = "Generate Cloudflared credentials from ghost token";
      before = [ "cloudflared-tunnel-${ghostTunnelId}.service" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = script;
        RemainAfterExit = true;
      };
    };

  systemd.services."cloudflared-tunnel-${ghostTunnelId}" = {
    requires = [ "cloudflared-credentials-ghost.service" ];
    after = [ "cloudflared-credentials-ghost.service" ];
  };
}
