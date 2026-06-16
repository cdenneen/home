{ lib, config, ... }:
let
  inputs = config._module.args.inputs or { };
in {
  options.services.jarvis = {
    enable = lib.mkEnableOption "Jarvis core/node deployment wrapper";

    role = lib.mkOption {
      type = lib.types.enum [ "core" "node" ];
      default = "core";
      description = "Select whether this host runs Jarvis core services or a node runner.";
    };
  };

  imports = [
    ../../../../hosts/nixos/jarvis-ghost.nix
    ../../../../hosts/nixos/jarvis-nyx.nix
  ];

  config = lib.mkIf config.services.jarvis.enable {
    assertions = [
      {
        assertion = inputs ? jarvis;
        message = "Jarvis flake input (inputs.jarvis) is required for services.jarvis";
      }
    ];
  };
}
