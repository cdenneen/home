{ inputs, self }:
let
  inherit (inputs)
    catppuccin
    nix-index-database
    nur
    sops-nix
    ;
in
{
  sharedHomeModulesIntegrated = [
    catppuccin.homeModules.catppuccin
    nix-index-database.homeModules.nix-index
    nur.modules.homeManager.default
    self.homeModules.default
    sops-nix.homeManagerModules.sops
  ];

  sharedHomeModulesStandalone = [
    catppuccin.homeModules.catppuccin
    nix-index-database.homeModules.nix-index
    nur.modules.homeManager.default
    self.homeModules.default
    sops-nix.homeManagerModules.sops
  ];

  mkPkgs = system: {
    stable = self.lib.import_nixpkgs system inputs.nixpkgs-stable;
    unstable = self.lib.import_nixpkgs system inputs.nixpkgs-unstable;
  };
}
