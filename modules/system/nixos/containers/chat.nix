{ config, lib, ... }:
let
  cfg = config.containerPresets.open-webui;
  useHostNetwork = cfg.ollamaBaseUrl == "http://127.0.0.1:11434";
in
{
  options.containerPresets.open-webui = {
    enable = lib.mkEnableOption "Enable open-webui";
    port = lib.mkOption {
      type = lib.types.int;
      default = 8080;
      description = "Port to expose open-webui on";
    };
    dataDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/open-webui";
      description = "Path to store open-webui data";
    };
    ollamaBaseUrl = lib.mkOption {
      type = lib.types.str;
      default = "https://ollama.diekvoss.net";
      description = "Ollama API base URL for Open WebUI.";
    };
    openFirewall = lib.mkEnableOption "Enable open-webui firewall rules";
  };

  config = lib.mkIf cfg.enable {
    containerPresets.podman.enable = lib.mkDefault true;
    virtualisation.arion.projects.open-webui.settings.services.open-webui.service = {
      image = "ghcr.io/open-webui/open-webui:v0.11.0@sha256:72c0ba641ba75e7aa52655cb242570906ececd09b1140fb736483038a22b3228";
      ports = lib.optionals (!useHostNetwork) [ "${toString cfg.port}:8080" ];
      volumes = [
        "${cfg.dataDir}:/app/backend/data"
      ];
      environment = {
        OLLAMA_BASE_URL = cfg.ollamaBaseUrl;
      };
    }
    // lib.optionalAttrs useHostNetwork {
      network_mode = "host";
    };
    networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall [ cfg.port ];
  };
}
