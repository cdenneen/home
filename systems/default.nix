{
  apple-silicon-support,
  catppuccin,
  disko,
  home-manager,
  mac-app-util,
  nh_plus,
  nix-darwin,
  nix-index-database,
  nixos-cosmic,
  nixos-hardware,
  nixpkgs,
  nixos-wsl,
  nur,
  nur-packages,
  nvf,
  self,
  sops-nix,
  ...
}@inputs:
let
  sharedHomeManagerModules = [
    catppuccin.homeManagerModules.catppuccin
    nix-index-database.hmModules.nix-index
    nur.modules.homeManager.default
    nvf.homeManagerModules.nvf
    self.homeManagerModules.default
    sops-nix.homeManagerModules.sops
  ];
  lib = nixpkgs.lib;
  nixosSystem =
    {
      system,
      nixosModules ? [ ],
      homeManagerModules ? [ ],
    }:
    let
      pkgs = self.lib.import_nixpkgs { inherit system; };
      specialArgs = inputs // {
        inherit system;
      };
    in
    lib.nixosSystem {
      inherit system pkgs;
      specialArgs = specialArgs;
      modules = [
        catppuccin.nixosModules.catppuccin
        disko.nixosModules.disko
        home-manager.nixosModules.default
        nix-index-database.nixosModules.nix-index
        nixos-cosmic.nixosModules.default
        nixpkgs.nixosModules.notDetected
        nur.modules.nixos.default
        nur-packages.nixosModules.cloudflare-ddns
        self.nixosModules.default
        sops-nix.nixosModules.sops
        {
          home-manager = {
            extraSpecialArgs = specialArgs;
            sharedModules =
              homeManagerModules
              ++ sharedHomeManagerModules;
          };
        }
      ] ++ nixosModules;
    };
  darwinSystem =
    {
      system,
      darwinModules ? [ ],
      homeManagerModules ? [ ],
    }:
    let
      pkgs = self.lib.import_nixpkgs { inherit system; };
      specialArgs = inputs // {
        inherit system;
      };
    in
    nix-darwin.lib.darwinSystem {
      inherit pkgs;
      specialArgs = specialArgs;
      modules = [
        home-manager.darwinModules.default
        mac-app-util.darwinModules.default
        nh_plus.nixDarwinModules.prebuiltin
        nix-index-database.darwinModules.nix-index
        self.darwinModules.default
        {
          home-manager = {
            extraSpecialArgs = specialArgs;
            sharedModules = [
              mac-app-util.homeManagerModules.default
            ] ++ homeManagerModules ++ sharedHomeManagerModules;
          };
        }
      ] ++ darwinModules;
    };
  homeConfiguration =
    {
      system,
      homeManagerModules ? [ ],
    }:
    let
      pkgs = self.lib.import_nixpkgs { inherit system; };
      specialArgs = inputs // {
        inherit system;
      };
    in
    home-manager.lib.homeManagerConfiguration {
      inherit pkgs;
      extraSpecialArgs = specialArgs;
      modules = homeManagerModules ++ sharedHomeManagerModules;
    };
in
{
  darwinConfigurations = {
    VNJTECMBCD= darwinSystem {
      system = "aarch64-darwin";
      darwinModules = [ ./mac.nix ];
    };
    mbair = darwinSystem {
      system = "x86_64-darwin";
      darwinModules = [ ./MacBookAir-Intel.nix ];
    };
  };
  homeConfigurations = {
    "hm@linux-x86" = homeConfiguration {
      system = "x86_64-linux";
      homeManagerModules = [
        ./home.nix
      ];
    };
    "hm@linux" = homeConfiguration {
      system = "aarch64-linux";
      homeManagerModules = [
        ./home.nix
      ];
    };
    "hm@mac" = homeConfiguration {
      system = "aarch64-darwin";
      homeManagerModules = [
        ./home.nix
      ];
    };
  };
  nixosConfigurations = {
    eros = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./eros.nix
        "${nixpkgs}/nixos/modules/virtualisation/amazon-image.nix"
      ];
    };
    MacBook-Pro-Nixos = nixosSystem {
      system = "x86_64-linux";
      nixosModules = [
        ./MacBook-Pro-NixOS.nix
        "${nixpkgs}/nixos/modules/installer/scan/not-detected.nix"
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
        nixos-wsl.nixosModules.wsl
      ];
    };
  };
}
