{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.jarvisSubstrate;
  jarvisUser = "jarvis";
  apiUser = "cdenneen";
  persistenceRoot = "/var/lib/jarvis";
in
{
  options.services.jarvisSubstrate = {
    enable = lib.mkEnableOption "Jarvis storage and runtime substrate";

    enableStorageContainers = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Run PostgreSQL, Qdrant, and Neo4j containers on the host.";
    };

    enableAppContainers = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Reserved for future assistant-gateway/slack-gateway container deployment.";
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.${jarvisUser} = {
      isSystemUser = true;
      group = jarvisUser;
      extraGroups = [ "podman" ];
      home = persistenceRoot;
      homeMode = "0755";
      createHome = true;
    };
    users.groups.${jarvisUser} = { };

    virtualisation.podman = {
      enable = true;
      dockerCompat = true;
      defaultNetwork.settings.dns_enabled = true;
    };

    systemd.slices."jarvis-ai" = {
      description = "Inference Engine Resources Slice (16GB Bounds)";
      sliceConfig = {
        MemoryMax = "16G";
        CPUQuota = "250%";
        IOWeight = 100;
      };
    };

    systemd.slices."jarvis-system" = {
      description = "Core Storage and Gateway Resources Slice (8GB Bounds)";
      sliceConfig = {
        MemoryMax = "8G";
        CPUQuota = "130%";
        IOWeight = 80;
      };
    };

    systemd.tmpfiles.rules = [
      "z ${persistenceRoot} 0755 root root -"
      "d ${persistenceRoot}/postgres 0700 70 70 -"
      "d ${persistenceRoot}/qdrant 0750 ${jarvisUser} ${jarvisUser} -"
      "d ${persistenceRoot}/neo4j 0750 ${jarvisUser} ${jarvisUser} -"
      "d ${persistenceRoot}/neo4j/data 0750 ${jarvisUser} ${jarvisUser} -"
      "d ${persistenceRoot}/neo4j/import 0750 ${jarvisUser} ${jarvisUser} -"
      "d ${persistenceRoot}/data 0775 ${apiUser} users -"
      "d ${persistenceRoot}/ollama 0750 ${jarvisUser} ${jarvisUser} -"
      "d ${persistenceRoot}/logs 0750 ${jarvisUser} ${jarvisUser} -"
      "d ${persistenceRoot}/backups 0750 ${jarvisUser} ${jarvisUser} -"
      "d ${persistenceRoot}/docs 0750 ${jarvisUser} ${jarvisUser} -"
      "d ${persistenceRoot}/reflections 0750 ${jarvisUser} ${jarvisUser} -"
    ];

    virtualisation.oci-containers.backend = "podman";
    virtualisation.oci-containers.containers = lib.mkMerge [
      (lib.mkIf cfg.enableStorageContainers {
        jarvis-postgres = {
          image = "docker.io/library/postgres:16-alpine";
          environment = {
            POSTGRES_DB = "jarvis";
            POSTGRES_USER = "jarvis";
            POSTGRES_HOST_AUTH_METHOD = "trust";
          };
          volumes = [ "${persistenceRoot}/postgres:/var/lib/postgresql/data" ];
          ports = [ "127.0.0.1:5432:5432" ];
          extraOptions = [ "--health-cmd=pg_isready -U jarvis" ];
        };

        jarvis-qdrant = {
          image = "docker.io/qdrant/qdrant:latest";
          volumes = [ "${persistenceRoot}/qdrant:/qdrant/storage" ];
          ports = [ "127.0.0.1:6333:6333" ];
          extraOptions = [ ];
        };

        jarvis-neo4j = {
          image = "docker.io/library/neo4j:5-community";
          environment = {
            NEO4J_AUTH = "neo4j/jarvis_graph_secure_pass";
          };
          volumes = [
            "${persistenceRoot}/neo4j/data:/data"
            "${persistenceRoot}/neo4j/import:/var/lib/neo4j/import"
          ];
          ports = [
            "127.0.0.1:7474:7474"
            "127.0.0.1:7687:7687"
          ];
          extraOptions = [ ];
        };
      })

      (lib.mkIf cfg.enableAppContainers {
        assistant-gateway = {
          image = "jarvis-assistant-gateway:local";
          ports = [ "127.0.0.1:4000:4000" ];
          extraOptions = [ ];
          dependsOn = [
            "jarvis-postgres"
            "jarvis-qdrant"
            "jarvis-neo4j"
          ];
        };

        slack-gateway = {
          image = "jarvis-slack-gateway:local";
          extraOptions = [ ];
          dependsOn = [ "assistant-gateway" ];
        };
      })
    ];

    systemd.services = {
      podman-jarvis-postgres.serviceConfig.Slice = "jarvis-system.slice";
      podman-jarvis-qdrant.serviceConfig.Slice = "jarvis-system.slice";
      podman-jarvis-neo4j.serviceConfig.Slice = "jarvis-system.slice";
      podman-assistant-gateway.serviceConfig.Slice = "jarvis-ai.slice";
      podman-slack-gateway.serviceConfig.Slice = "jarvis-system.slice";
    };

    systemd.services.jarvis-model-warmup = {
      description = "Download and cache local AI models inside Ollama";
      after = [
        "ollama.service"
        "network-online.target"
      ];
      wants = [
        "ollama.service"
        "network-online.target"
      ];
      wantedBy = [ "multi-user.target" ];
      path = [
        pkgs.curl
        pkgs.coreutils
        pkgs.bash
      ];
      script = ''
        set -euo pipefail
        export OLLAMA_HOST="127.0.0.1:11434"
        until curl -fsS "http://$OLLAMA_HOST/api/tags" >/dev/null; do sleep 2; done
        curl -fsS -X POST "http://$OLLAMA_HOST/api/pull" -d '{"name": "llama3.1:8b-instruct-q4_K_M", "stream": false}' >/dev/null || true
        curl -fsS -X POST "http://$OLLAMA_HOST/api/pull" -d '{"name": "qwen2.5-coder:7b-instruct", "stream": false}' >/dev/null || true
        curl -fsS -X POST "http://$OLLAMA_HOST/api/pull" -d '{"name": "bge-large-en-v1.5", "stream": false}' >/dev/null || true
      '';
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
    };
  };
}
