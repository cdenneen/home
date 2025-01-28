{
  config,
  lib,
  self,
  ...
}@inputs:
let
  cfg = config.profiles;
in
{
  imports = [
    ./users
    # ./gui.nix
    # ./dev.nix
    # ./console.nix
  ];

  options.profiles.defaults.enable = lib.mkEnableOption "Enable Defaults";

  config = lib.mkIf cfg.defaults.enable {
    home-manager = {
      #backupFileExtension = "${self.shortRev or self.dirtyShortRev}.old";
      useGlobalPkgs = true;
      useUserPackages = true;
      sharedModules = [
        {
          nix.package = lib.mkForce config.nix.package;
        }
      ];
    };
    nix = {
      settings = {
        experimental-features = [
          "nix-command"
          "flakes"
        ];
        substituters = config.nix.settings.trusted-substituters;
        trusted-substituters = [
          "https://cache.nixos.org"
          "https://nix-community.cachix.org"
        ];
        trusted-public-keys = [
          "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
          "nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs="
        ];
      };
      nixPath = [
        "nixpkgs=${inputs.nixpkgs}"
      ];
    };
  };
}
