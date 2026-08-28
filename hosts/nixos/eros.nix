{
  config,
  pkgs,
  lib,
  ...
}:
let
  litellmPort = 4000;
  litellmEnvFile = "/run/eros-litellm/env";
  litellmConfigFile = "/run/eros-litellm/config.yaml";
  omniroutePort = 20128;
  qdrantPort = 6333;
in
{
  networking.hostName = "eros";
  ec2.efi = true;

  # CPU-only LLM gateway: retain the host tooling but do not run a desktop.
  profiles.gui.enable = false;
  services.desktopManager.plasma6.enable = false;
  services.xserver.desktopManager.xfce.enable = false;
  services.xserver.displayManager.lightdm.enable = false;
  services.displayManager.sddm.enable = false;

  services.tailscale = {
    enable = true;
    openFirewall = false;
  };

  # Keep the raw Ollama API local. Clients use the authenticated LiteLLM
  # gateway on the tailnet; do not expose port 11434.
  services.ollama = {
    enable = true;
    host = "127.0.0.1";
    openFirewall = false;
    loadModels = [
      "qwen2.5-coder:7b"
      "qwen3-embedding:0.6b"
    ];
    environmentVariables = {
      OLLAMA_MAX_LOADED_MODELS = "2";
      OLLAMA_CONTEXT_LENGTH = "32768";
      OLLAMA_NUM_PARALLEL = "1";
    };
  };

  virtualisation.oci-containers = {
    backend = "podman";
    containers = {
      qdrant = {
        image = "qdrant/qdrant:v1.18.3@sha256:0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286";
        ports = [ "127.0.0.1:${toString qdrantPort}:6333" ];
        volumes = [ "/var/lib/qdrant:/qdrant/storage:U" ];
        autoStart = true;
      };
      litellm = {
        image = "ghcr.io/berriai/litellm:v1.94.0@sha256:65d84a2282137b4dc73bbe184650a7c807177c533e4223b3bfbc87963fe3fabe";
        volumes = [ "${litellmConfigFile}:/app/config.yaml:ro" ];
        extraOptions = [
          "--env-file=${litellmEnvFile}"
          "--network=host"
        ];
        cmd = [
          "--config"
          "/app/config.yaml"
          "--host"
          "127.0.0.1"
          "--port"
          (toString litellmPort)
        ];
        autoStart = true;
      };
    };
  };

  systemd.tmpfiles.rules = [ "d /var/lib/qdrant 0750 root root -" ];

  services.postgresql = {
    enable = true;
    ensureDatabases = [ "litellm" ];
    ensureUsers = [
      {
        name = "litellm";
        ensureDBOwnership = true;
      }
    ];
    authentication = lib.mkAfter ''
      host litellm litellm 127.0.0.1/32 scram-sha-256
    '';
    settings.password_encryption = "scram-sha-256";
  };

  sops.secrets = {
    eros_litellm_master_key = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    eros_litellm_db_password = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    eros_litellm_salt_key = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    openai_api_key = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    gemini_api_key = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
  };

  systemd.services.eros-litellm-env = {
    description = "Render the LiteLLM secret environment";
    before = [ "podman-litellm.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail

      read_secret() {
        local secret_file="$1"
        local secret_name="$2"
        if [ ! -r "$secret_file" ]; then
          echo "Missing $secret_name at $secret_file" >&2
          exit 1
        fi
        ${pkgs.coreutils}/bin/tr -d '\n\r' < "$secret_file"
      }

      ${pkgs.coreutils}/bin/install -d -m 0700 /run/eros-litellm
      ${pkgs.coreutils}/bin/install -m 0600 /dev/null "${litellmEnvFile}"
      {
        printf 'LITELLM_MASTER_KEY=%s\n' "$(read_secret "${config.sops.secrets.eros_litellm_master_key.path}" "LiteLLM master key")"
        printf 'LITELLM_SALT_KEY=%s\n' "$(read_secret "${config.sops.secrets.eros_litellm_salt_key.path}" "LiteLLM salt key")"
        printf 'DATABASE_URL=postgresql://litellm:%s@127.0.0.1:5432/litellm\n' "$(read_secret "${config.sops.secrets.eros_litellm_db_password.path}" "LiteLLM database password")"
        printf 'OPENAI_API_KEY=%s\n' "$(read_secret "${config.sops.secrets.openai_api_key.path}" "OpenAI key")"
        printf 'GEMINI_API_KEY=%s\n' "$(read_secret "${config.sops.secrets.gemini_api_key.path}" "Gemini key")"
        printf 'QDRANT_API_BASE=http://127.0.0.1:%s\n' "${toString qdrantPort}"
        printf 'QDRANT_VECTOR_SIZE=1024\n'
      } > "${litellmEnvFile}"
    '';
  };

  systemd.services.eros-litellm-config = {
    description = "Render the LiteLLM configuration";
    before = [ "podman-litellm.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail
      ${pkgs.coreutils}/bin/install -d -m 0700 /run/eros-litellm
      cat > "${litellmConfigFile}" <<'EOF'
      model_list:
        # --- Legacy aliases (existing live consumers; kept for compatibility) ---
        - model_name: coding
          litellm_params:
            model: ollama/qwen2.5-coder:7b
            api_base: http://127.0.0.1:11434
          model_info:
            max_input_tokens: 28672
            max_output_tokens: 4096
        - model_name: local-embed
          litellm_params:
            model: ollama/qwen3-embedding:0.6b
            api_base: http://127.0.0.1:11434
        - model_name: coding-openai
          litellm_params:
            model: openai/gpt-5-mini
            api_key: os.environ/OPENAI_API_KEY
        - model_name: openai/*
          litellm_params:
            model: openai/*
            api_key: os.environ/OPENAI_API_KEY
        - model_name: coding-haiku
          litellm_params:
            model: bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
            aws_region_name: us-east-1
        - model_name: coding-gemini
          litellm_params:
            model: gemini/gemini-2.5-flash
            api_key: os.environ/GEMINI_API_KEY
        # Fixed 2026-08-27: was bedrock/us.anthropic.claude-sonnet-4-6 ($3/$15 per
        # Mtok), which is *more expensive* than Sonnet 5 ($2/$10 per Mtok) despite
        # being the older model. Live traffic was silently overpaying since this
        # route went into use. See phase1-attribution-and-counterfactual.md.
        - model_name: coding-strong
          litellm_params:
            model: bedrock/us.anthropic.claude-sonnet-5
            aws_region_name: us-east-1

        # --- Stable capability-tier routes (00-program-spec.md route contract) ---
        - model_name: tier0-local
          litellm_params:
            model: ollama/qwen2.5-coder:7b
            api_base: http://127.0.0.1:11434
          model_info:
            max_input_tokens: 28672
            max_output_tokens: 4096
        # additional_drop_params below (tier1-general, tier2-general/coding/
        # research): Hermes's sitecustomize patch unconditionally injects a
        # descriptive `x_hermes_source` field into every chat-completion
        # request. Bedrock's Converse API validates strictly and rejects
        # unrecognized fields ("x_hermes_source: Extra inputs are not
        # permitted") - confirmed live during the Nyx GitLab canary. The
        # global litellm_settings.drop_params/additional_drop_params below
        # do NOT reach this per-request field (they only filter recognized
        # OpenAI-SDK params); it must be set on each affected model's own
        # litellm_params to actually take effect. Applied to tier1-general
        # defensively even though Gemini wasn't observed to reject it.
        - model_name: tier1-general
          litellm_params:
            model: gemini/gemini-2.5-flash
            api_key: os.environ/GEMINI_API_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: tier1-coding
          litellm_params:
            model: openai/gpt-5-mini
            api_key: os.environ/OPENAI_API_KEY
        - model_name: tier2-general
          litellm_params:
            model: bedrock/us.anthropic.claude-sonnet-5
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: tier2-coding
          litellm_params:
            model: bedrock/us.anthropic.claude-sonnet-5
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: tier2-research
          litellm_params:
            model: bedrock/us.anthropic.claude-sonnet-5
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: tier3-quality
          litellm_params:
            model: bedrock/global.anthropic.claude-opus-5
            aws_region_name: us-east-1
        # tier4-frontier deliberately has no fallback and is not part of any
        # fallback chain below - explicit-only, separate key at the governor
        # layer (00-program-spec.md: "explicit only; no automatic fallback").
        - model_name: tier4-frontier
          litellm_params:
            model: openai/gpt-5.6-sol
            api_key: os.environ/OPENAI_API_KEY
      general_settings:
        master_key: os.environ/LITELLM_MASTER_KEY
        database_url: os.environ/DATABASE_URL
      litellm_settings:
        # Bootstrap default is cache bypass everywhere (01-eros-inference-fabric.md).
        # Previously cache:true + router_settings.cache_responses:false were both
        # present at once (ambiguous, flagged in 07-cache-and-retrieval.md) - fixed
        # by disabling caching outright until 07's adoption sequence is run.
        cache: false
        drop_params: true
      router_settings:
        cache_responses: false
        # No cross-tier fallbacks: 01-eros-inference-fabric.md prohibits generic
        # fallback ("Generic 429/error fallback is prohibited"), and the previous
        # `coding-strong: [coding-gemini]` entry silently collapsed a Claude-tier
        # request onto Gemini Flash on failure - undetectable capability
        # downgrade. Routing rejects rather than silently downgrades; the
        # host-local policy endpoint / continuity controller owns any approved
        # continuity path, not the LiteLLM router.
        num_retries: 1
        timeout: 90
      EOF
      ${pkgs.coreutils}/bin/chmod 0600 "${litellmConfigFile}"
    '';
  };

  systemd.services.eros-litellm-db-user = {
    description = "Set the LiteLLM PostgreSQL role password";
    after = [ "postgresql.service" ];
    requires = [ "postgresql.service" ];
    before = [ "podman-litellm.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [
      config.services.postgresql.package
      pkgs.coreutils
      pkgs.util-linux
    ];
    script = ''
      set -euo pipefail

      password_file="${config.sops.secrets.eros_litellm_db_password.path}"
      if [ ! -r "$password_file" ]; then
        echo "Missing LiteLLM database password at $password_file" >&2
        exit 1
      fi

      password="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$password_file")"
      if [ -z "$password" ]; then
        echo "LiteLLM database password is empty" >&2
        exit 1
      fi
      case "$password" in
        *[!0-9a-f]*)
          echo "LiteLLM database password must be lowercase hexadecimal" >&2
          exit 1
          ;;
      esac

      printf "ALTER ROLE litellm PASSWORD '%s';\\n" "$password" \
        | ${pkgs.util-linux}/bin/runuser -u postgres -- ${config.services.postgresql.package}/bin/psql --dbname=postgres --set=ON_ERROR_STOP=1
    '';
  };

  systemd.services.podman-litellm = {
    requires = [
      "eros-litellm-db-user.service"
      "eros-litellm-env.service"
      "eros-litellm-config.service"
      "ollama.service"
      "podman-qdrant.service"
    ];
    after = [
      "eros-litellm-db-user.service"
      "eros-litellm-env.service"
      "eros-litellm-config.service"
      "ollama.service"
      "podman-qdrant.service"
    ];
  };

  systemd.services.omniroute = {
    description = "OmniRoute local AI gateway";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    path = [ pkgs.nodejs_24 ];
    environment = {
      HOME = config.users.users.cdenneen.home;
      HOSTNAME = "127.0.0.1";
      PORT = toString omniroutePort;
      DATA_DIR = "${config.users.users.cdenneen.home}/.omniroute";
    };
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = config.users.users.cdenneen.home;
      ExecStart = "${config.users.users.cdenneen.home}/.local/bin/omniroute --no-open";
      Restart = "on-failure";
      RestartSec = "5s";
    };
  };

  systemd.services.tailscale-serve-eros = {
    description = "Expose LiteLLM and OmniRoute over Tailscale";
    after = [
      "tailscaled.service"
      "podman-litellm.service"
      "omniroute.service"
    ];
    requires = [
      "tailscaled.service"
      "podman-litellm.service"
      "omniroute.service"
    ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig.Type = "oneshot";
    path = [ pkgs.tailscale ];
    script = ''
      set -euo pipefail
      if ! ${pkgs.tailscale}/bin/tailscale status >/dev/null 2>&1; then
        echo "Tailscale is not authenticated; skipping LiteLLM serve"
        exit 0
      fi
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString litellmPort} 127.0.0.1:${toString litellmPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString omniroutePort} 127.0.0.1:${toString omniroutePort}
    '';
  };

  services.udisks2.enable = lib.mkForce false;
  services.openssh.settings.PermitRootLogin = lib.mkForce "prohibit-password";
  users.users.cdenneen.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAII1avpzyzr4rhp/LyD9JrcO+DJP+6pBMwbOglSBXHudF cdenneen_ed25519_2024"
  ];

  services.amazon-cloudwatch-agent = {
    enable = true;
    mode = "ec2";
    user = "root";
    commonConfiguration = {
      credentials = {
        imds_version = 2;
      };
    };
    configuration = {
      agent = {
        metrics_collection_interval = 60;
        region = "us-east-1";
        logfile = "/var/log/amazon-cloudwatch-agent/amazon-cloudwatch-agent.log";
      };
      metrics = {
        namespace = "CWAgent";
        append_dimensions = {
          ImageId = "\${aws:ImageId}";
          InstanceId = "\${aws:InstanceId}";
          InstanceType = "\${aws:InstanceType}";
          AutoScalingGroupName = "\${aws:AutoScalingGroupName}";
        };
        aggregation_dimensions = [ [ "InstanceId" ] ];
        metrics_collected = {
          cpu = {
            measurement = [
              "cpu_usage_idle"
              "cpu_usage_iowait"
              "cpu_usage_user"
              "cpu_usage_system"
            ];
            totalcpu = true;
            metrics_collection_interval = 60;
          };
          mem = {
            measurement = [
              "mem_used_percent"
              "mem_available"
              "mem_available_percent"
            ];
            metrics_collection_interval = 60;
          };
          disk = {
            measurement = [ "used_percent" ];
            resources = [ "/" ];
            drop_device = true;
            metrics_collection_interval = 60;
          };
          diskio = {
            measurement = [
              "reads"
              "writes"
              "read_bytes"
              "write_bytes"
              "io_time"
            ];
            resources = [ "*" ];
            metrics_collection_interval = 60;
          };
          net = {
            measurement = [
              "bytes_sent"
              "bytes_recv"
            ];
            resources = [ "*" ];
            metrics_collection_interval = 60;
          };
          swap = {
            measurement = [ "used_percent" ];
            metrics_collection_interval = 60;
          };
          processes = {
            measurement = [
              "running"
              "sleeping"
              "zombies"
              "total"
            ];
            metrics_collection_interval = 60;
          };
        };
      };
    };
  };

  services.amazon-ssm-agent.enable = true;

  # Matches running system (do not change after initial install)
  # Match global default; do not downgrade
  system.stateVersion = lib.mkForce "26.05";

  # Filesystems.
  # NOTE: Some upstream EC2/EFI modules also declare an ESP at /boot.
  # We force the full fileSystems attrset here so the ESP is only mounted
  # at /boot/efi; /boot must remain on the root filesystem for NixOS kernels.
  fileSystems = lib.mkForce {
    "/" = {
      device = "/dev/disk/by-uuid/f222513b-ded1-49fa-b591-20ce86a2fe7f";
      fsType = "ext4";
    };

    "/boot/efi" = {
      device = "/dev/disk/by-uuid/12CE-A600";
      fsType = "vfat";
    };
  };

  # Leave /boot on the root filesystem; only mount the ESP at /boot/efi.
  # This avoids running out of space on the ESP when storing kernels/initrd.

  # UEFI + GRUB (current system uses GRUB on EFI)
  boot.loader = {
    efi = {
      # EC2 UEFI typically does not provide persistent EFI variables.
      canTouchEfiVariables = false;
      efiSysMountPoint = "/boot/efi";
    };
    grub = {
      splashImage = lib.mkForce null;
      enable = true;
      configurationLimit = 3;
      efiSupport = true;
      # Install via the UEFI removable-media fallback path (EFI/BOOT).
      efiInstallAsRemovable = true;
      device = "nodev";
    };
  };

  # On EC2 we install GRUB as "removable" (EFI/BOOT/BOOTAA64.EFI). In that mode
  # GRUB tends to use the ESP for configuration, which is too small for storing
  # NixOS kernels/initrds across generations.
  #
  # We generate a small ESP grub.cfg that:
  # - First entry chainloads the real GRUB menu from the root filesystem.
  # - Second entry boots the currently selected system profile directly.
  #
  # Important: avoid referencing config.system.build.* here; it can create module
  # evaluation recursion. Use stable on-disk paths instead.
  boot.loader.grub.extraInstallCommands = ''
        ${pkgs.coreutils}/bin/mkdir -p "${config.boot.loader.efi.efiSysMountPoint}/grub"
        ${pkgs.coreutils}/bin/cat > "${config.boot.loader.efi.efiSysMountPoint}/grub/grub.cfg" <<'EOF'
        # Autogenerated (NixOS): ESP GRUB config for EC2.
        set timeout=1
        set timeout_style=menu
        set default=0

        function chainload_rootfs_menu {
          insmod part_gpt
          insmod ext2
          insmod search_fs_file
          if search --no-floppy --file /boot/grub/grub.cfg --set=root; then
            set prefix=($root)/boot/grub
            configfile ($root)/boot/grub/grub.cfg
          fi
        }

        function boot_current_profile {
          insmod part_gpt
          insmod ext2
          insmod search_fs_file
          insmod linux
          if search --no-floppy --file /nix/var/nix/profiles/system/init --set=root; then
            linux ($root)/nix/var/nix/profiles/system/kernel init=/nix/var/nix/profiles/system/init console=ttyS0,115200n8
            initrd ($root)/nix/var/nix/profiles/system/initrd
            boot
          fi
        }

        menuentry "NixOS (full menu)" --class nixos --unrestricted {
          chainload_rootfs_menu
          echo "GRUB: failed to chainload /boot/grub/grub.cfg"
          sleep 5
        }

        menuentry "NixOS (current system profile)" --class nixos --unrestricted {
          boot_current_profile
          echo "GRUB: failed to boot /nix/var/nix/profiles/system"
          sleep 5
        }
    EOF
  '';

  # Networking (DHCP on ens5)
  networking.useDHCP = false;
  networking.interfaces.ens5.useDHCP = true;

  profiles.defaults.enable = true;
}
