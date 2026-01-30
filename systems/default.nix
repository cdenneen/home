{
  apple-silicon-support,
  catppuccin,
  disko,
  home-manager,
  mac-app-util,
  nh_plus,
  nix-darwin,
  nix-index-database,
  nixos-hardware,
  nixpkgs-unstable,
  nixpkgs-stable,
  nixpkgs-esp-dev,
  nixos-wsl,
  nur,
  nur-packages,
  nvf,
  rust-overlay,
  self,
  sops-nix,
  ...
}@inputs:
let
  sharedHomeModules = [
    catppuccin.homeModules.catppuccin
    nix-index-database.homeModules.nix-index
    nur.modules.homeManager.default
    nvf.homeManagerModules.nvf
    self.homeModules.default
    sops-nix.homeManagerModules.sops
    {
      nixpkgs = {
        overlays = [
          (import rust-overlay)
          nixpkgs-esp-dev.overlays.default
          nur-packages.overlays.default
        ];
        config = {
          allowUnfree = true;
          allowBroken = true;
        };
      };
    }
  ];
  lib = nixpkgs-unstable.lib;
  nixosSystem =
    {
      system,
      nixosModules ? [ ],
      homeModules ? [ ],
      pkgsFrom ? "stable",
    }:
    let
      unstablePkgs = self.lib.import_nixpkgs system nixpkgs-unstable;
      stablePkgs = self.lib.import_nixpkgs system nixpkgs-stable;
      selectedPkgs = if pkgsFrom == "unstable" then unstablePkgs else stablePkgs;
    in
    lib.nixosSystem rec {
      inherit system;
      pkgs = selectedPkgs;
      specialArgs = inputs // {
        inherit
          system
          stablePkgs
          unstablePkgs
          ;
      };
      modules = [
        catppuccin.nixosModules.catppuccin
        disko.nixosModules.disko
        home-manager.nixosModules.default
        nix-index-database.nixosModules.nix-index
        nixpkgs-unstable.nixosModules.notDetected
        nur.modules.nixos.default
        #nur-packages.nixosModules.cloudflare-ddns
        self.nixosModules.default
        sops-nix.nixosModules.sops
        {
          home-manager = {
            extraSpecialArgs = specialArgs;
            sharedModules = homeModules ++ sharedHomeModules;
          };
        }
      ]
      ++ nixosModules;
    };
  darwinSystem =
    {
      system,
      darwinModules ? [ ],
      homeModules ? [ ],
    }:
    let
      unstablePkgs = self.lib.import_nixpkgs system nixpkgs-unstable;
      stablePkgs = self.lib.import_nixpkgs system nixpkgs-stable;
    in
    nix-darwin.lib.darwinSystem rec {
      pkgs = stablePkgs;
      specialArgs = inputs // {
        inherit
          system
          stablePkgs
          unstablePkgs
          ;
      };
      modules = [
        home-manager.darwinModules.default
        mac-app-util.darwinModules.default
        #nh_plus.nixDarwinModules.prebuiltin
        nix-index-database.darwinModules.nix-index
        self.darwinModules.default
        sops-nix.darwinModules.sops
        {
          home-manager = {
            backupFileExtension = "${self.shortRev or self.dirtyShortRev}.old";
            extraSpecialArgs = specialArgs;
            sharedModules = [
              mac-app-util.homeManagerModules.default
            ]
            ++ homeModules
            ++ sharedHomeModules;
          };
        }
      ]
      ++ darwinModules;
    };
  homeConfiguration =
    {
      system,
      homeModules ? [ ],
    }:
    let
      unstablePkgs = self.lib.import_nixpkgs system nixpkgs-unstable;
      stablePkgs = self.lib.import_nixpkgs system nixpkgs-stable;
    in
    home-manager.lib.homeManagerConfiguration {
      pkgs = stablePkgs;
      extraSpecialArgs = inputs // {
        inherit
          system
          stablePkgs
          unstablePkgs
          ;
      };
      modules = homeModules ++ sharedHomeModules;
    };
in
{
  lib = {
    inherit darwinSystem homeConfiguration nixosSystem;
  };
  darwinConfigurations = {
    VNJTECMBCD = darwinSystem {
      system = "aarch64-darwin";
      darwinModules = [ ./VNJTECMBCD ];
    };
    mac = darwinSystem {
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
      homeModules = [
        ./home.nix
      ];
    };
    "hm@linux" = homeConfiguration {
      system = "aarch64-linux";
      homeModules = [
        ./home.nix
      ];
    };
    "hm@mac" = homeConfiguration {
      system = "aarch64-darwin";
      homeModules = [
        ./home.nix
      ];
    };
  };
  nixosConfigurations = {
    eros = nixosSystem {
      system = "aarch64-linux";
      pkgsFrom = "unstable";
      nixosModules = [
        ./eros.nix
        "${nixpkgs-unstable}/nixos/modules/virtualisation/amazon-image.nix"
      ];
    };
    MacBook-Pro-Nixos = nixosSystem {
      system = "x86_64-linux";
      nixosModules = [
        ./MacBook-Pro-NixOS.nix
        "${nixpkgs-unstable}/nixos/modules/installer/scan/not-detected.nix"
      ];
    };
    oracle-cloud-nixos = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./oracle-cloud-nixos.nix
        "${nixpkgs-unstable}/nixos/modules/profiles/qemu-guest.nix"
      ];
    };
    utm = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./utm.nix
        "${nixpkgs-unstable}/nixos/modules/profiles/qemu-guest.nix"
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
