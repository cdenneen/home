{
  inputs,
  self,
  lib,
}:
let
  inherit (inputs)
    arion
    catppuccin
    disko
    home-manager
    nix-index-database
    nixpkgs
    nur
    sops-nix
    ;
  inherit (lib) mkPkgs sharedHomeModulesIntegrated;

  nixosSystem =
    {
      system,
      nixosModules ? [ ],
      homeModules ? [ ],
    }:
    let
      pkgsSet = mkPkgs system;
      stablePkgs = pkgsSet.stable;
      unstablePkgs = pkgsSet.unstable;
    in
    nixpkgs.lib.nixosSystem rec {
      inherit system;
      pkgs = stablePkgs;
      specialArgs = inputs // {
        inherit system stablePkgs unstablePkgs;
      };
      modules = [
        self.commonModules.users.cdenneen
        arion.nixosModules.arion
        catppuccin.nixosModules.catppuccin
        disko.nixosModules.disko
        home-manager.nixosModules.default
        nix-index-database.nixosModules.nix-index
        nixpkgs.nixosModules.notDetected
        nur.modules.nixos.default
        self.nixosModules.default
        sops-nix.nixosModules.sops
        {
          home-manager = {
            extraSpecialArgs = specialArgs;
            sharedModules = homeModules ++ sharedHomeModulesIntegrated;
          };
        }
      ]
      ++ nixosModules;
    };

  nixosConfigurations = {
    eros = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./ec2-base.nix
        ./eros.nix
        "${nixpkgs}/nixos/modules/virtualisation/amazon-image.nix"
      ];
    };
    eros-ec2 = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./ec2-base.nix
        ./eros-ec2.nix
        "${nixpkgs}/nixos/modules/virtualisation/amazon-image.nix"
      ];
    };
    amazon-ami = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./ec2-base.nix
        ./amazon-ami.nix
        "${nixpkgs}/nixos/maintainers/scripts/ec2/amazon-image.nix"
      ];
    };
    nyx = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./ec2-base.nix
        ./nyx.nix
        "${nixpkgs}/nixos/modules/virtualisation/amazon-image.nix"
      ];
    };
    oracle-cloud-nixos = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./oracle-cloud-nixos.nix
        "${nixpkgs}/nixos/modules/profiles/qemu-guest.nix"
      ];
    };
    utm = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./utm.nix
        "${nixpkgs}/nixos/modules/profiles/qemu-guest.nix"
      ];
    };
    wsl = nixosSystem {
      system = "x86_64-linux";
      nixosModules = [
        ./wsl.nix
        inputs.nixos-wsl.nixosModules.wsl
      ];
    };
  };
in
{
  inherit nixosConfigurations nixosSystem;
}
