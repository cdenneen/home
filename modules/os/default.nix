{
  config,
  lib,
  self,
  ...
}@inputs:
let
  cfg = config.profiles;
  nixSubstituters = [
    "https://cache.nixos.org"
    "https://nix-community.cachix.org"
    "https://cdenneen.cachix.org"
  ];
  nixPublicKeys = [
    "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
    "nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs="
    "cdenneen.cachix.org-1:EUognwSf1y0FAzDOPmUuYtz6aOxCWyNbcMi8PjHV8gU="
  ];
in
{
  imports = [
    ./users
    ./gui.nix
    ./dev.nix
    ./console.nix
  ];

  options.profiles.defaults.enable = lib.mkEnableOption "Enable Defaults";

  config = lib.mkIf cfg.defaults.enable {
    home-manager = {
      backupFileExtension = "${self.shortRev or self.dirtyShortRev}.old";
      useUserPackages = true;
      sharedModules = [
        {
          nix.package = lib.mkForce config.nix.package;
          home.sessionVariables.NIXPKGS_ALLOW_UNFREE = 1;
        }
      ];
    };
    nix = {
      settings = {
        experimental-features = [
          "nix-command"
          "flakes"
        ];
        substituters = nixSubstituters;
        trusted-substituters = nixSubstituters;
        trusted-public-keys = nixPublicKeys;
      };
      nixPath = [
        "nixpkgs=${inputs.nixpkgs-unstable}"
      ];
    };
  };
}
