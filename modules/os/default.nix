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
    ./gui.nix
    ./dev.nix
    ./console.nix
  ];

  options.profiles.defaults.enable = lib.mkEnableOption "Enable Defaults";

  config = lib.mkIf cfg.defaults.enable {
    # security.sudo.wheelNeedsPassword = false;
    home-manager = {
      backupFileExtension = "${self.shortRev or self.dirtyShortRev}.old";
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
          "https://cosmic.cachix.org"
          "https://cdenneen.cachix.org"
        ];
        trusted-public-keys = [
          "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
          "cosmic.cachix.org-1:Dya9IyXD4xdBehWjrkPv6rtxpmMdRel02smYzA85dPE="
          "cdenneen.cachix.org-1:EUognwSf1y0FAzDOPmUuYtz6aOxCWyNbcMi8PjHV8gU="
        ];
      };
      nixPath = [
        "nixpkgs=${inputs.nixpkgs}"
      ];
    };
  };
}
