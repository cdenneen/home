{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.containerPresets;
in
{
  imports = [
    ./chat.nix
    ./portainer.nix
  ];

  options.containerPresets = {
    podman.enable = lib.mkEnableOption "Enable podman runtime";
  };

  config = lib.mkIf cfg.podman.enable {
    users.groups.podman = { };
    virtualisation.podman = {
      enable = true;
      defaultNetwork.settings.dns_enabled = true;
      dockerCompat = true;
      dockerSocket.enable = true;
    };
    virtualisation.arion = {
      backend = "podman-socket";
      docker.client.package = lib.mkForce pkgs.docker_29;
    };
  };
}
