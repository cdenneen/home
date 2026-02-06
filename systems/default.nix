{
  apple-silicon-support,
  arion,
  catppuccin,
  discord_bot,
  disko,
  home-manager,
  jovian,
  mac-app-util,
  nh,
  nix-darwin,
  nix-index-database,
  nixos-hardware,
  nixos-wsl,
  nixpkgs-esp-dev,
  nixpkgs-stable,
  nixpkgs-unstable,
  nur,
  nur-packages,
  nvf,
  plasma-manager,
  rust-overlay,
  self,
  sops-nix,
  zed,
  ...
}@inputs:
let
  # NOTE: NVF (Neovim Framework) temporarily disabled on Darwin.
  # It pulls in Swift / .NET toolchains which are too expensive to build locally.
  # We will reintroduce this behind a proper dev profile later.
  sharedHomeModulesIntegrated = [
    catppuccin.homeModules.catppuccin
    nh.homeManagerModules.default
    nix-index-database.homeModules.nix-index
    nur.modules.homeManager.default
    nvf.homeManagerModules.nvf
    self.homeModules.default
    sops-nix.homeManagerModules.sops
  ];

  sharedHomeModulesStandalone = sharedHomeModulesIntegrated;
  homelab = import ../homelab.nix;
  lib = nixpkgs-unstable.lib;
  nixosSystem =
    {
      system,
      nixosModules ? [ ],
      homeModules ? [ ],
    }:
    let
      unstablePkgs = self.lib.import_nixpkgs system nixpkgs-unstable;
      stablePkgs = self.lib.import_nixpkgs system nixpkgs-stable;
    in
    lib.nixosSystem rec {
      inherit system;
      pkgs = unstablePkgs;
      specialArgs = inputs // {
        inherit
          system
          homelab
          stablePkgs
          unstablePkgs
          ;
      };
      modules = [
        self.commonModules.users.cdenneen
        arion.nixosModules.arion
        catppuccin.nixosModules.catppuccin
        discord_bot.nixosModules.discord_bot
        disko.nixosModules.disko
        home-manager.nixosModules.default
        nh.nixosModules.default
        nix-index-database.nixosModules.nix-index
        nixpkgs-unstable.nixosModules.notDetected
        nur.modules.nixos.default
        self.nixosModules.default
        sops-nix.nixosModules.sops
        {
          home-manager = {
            extraSpecialArgs = specialArgs;
            sharedModules =
              homeModules ++ sharedHomeModulesIntegrated ++ [ plasma-manager.homeModules.plasma-manager ];
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
      pkgs = unstablePkgs;
      specialArgs = inputs // {
        inherit
          system
          homelab
          stablePkgs
          unstablePkgs
          ;
      };
      modules = [
        self.commonModules.users.cdenneen
        home-manager.darwinModules.default
        inputs.nix-homebrew.darwinModules.nix-homebrew
        mac-app-util.darwinModules.default
        nh.nixDarwinModules.prebuiltin
        nix-index-database.darwinModules.nix-index
        nur.modules.darwin.default
        self.darwinModules.default
        sops-nix.darwinModules.sops
        {
          home-manager = {
            extraSpecialArgs = specialArgs;
            sharedModules = [
              mac-app-util.homeManagerModules.default
            ]
            ++ homeModules
            ++ sharedHomeModulesIntegrated;
          };
          homebrew = {
            enable = true;
            user = "cdenneen";
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
      pkgs = unstablePkgs;
      extraSpecialArgs = inputs // {
        inherit
          system
          homelab
          stablePkgs
          unstablePkgs
          ;
      };
      modules = homeModules ++ sharedHomeModulesStandalone;
    };
in
{
  lib = {
    inherit darwinSystem homeConfiguration nixosSystem;
  };
  darwinConfigurations = {
    VNJTECMBCD = darwinSystem {
      system = "aarch64-darwin";
      darwinModules = [ ./VNJTECMBCD.nix ];
    };
    MacBook-Pro = darwinSystem {
      system = "aarch64-darwin";
      darwinModules = [ ./MacBook-Pro.nix ];
    };
    MacMini-Intel = darwinSystem {
      system = "x86_64-darwin";
      darwinModules = [ ./MacMini-Intel.nix ];
    };
    MacMini-M1 = darwinSystem {
      system = "aarch64-darwin";
      darwinModules = [ ./MacMini-M1.nix ];
    };
  };
  homeConfigurations = {
    "deck@steamdeck" = homeConfiguration {
      system = "x86_64-linux";
      homeModules = [
        ./steamdeck.nix
        plasma-manager.homeModules.plasma-manager
      ];
    };
  };
  nixosConfigurations = {
    eros = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./eros.nix
        "${nixpkgs-unstable}/nixos/modules/virtualisation/amazon-image.nix"
      ];
    };
    eros-ec2 = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./ec2-base.nix
        ./eros-ec2.nix
        "${nixpkgs-unstable}/nixos/modules/virtualisation/amazon-image.nix"
      ];
    };
    amazon-ami = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./ec2-base.nix
        ./amazon-ami.nix
        "${nixpkgs-unstable}/nixos/maintainers/scripts/ec2/amazon-image.nix"
      ];
    };
    nyx = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./ec2-base.nix
        ./nyx.nix
        "${nixpkgs-unstable}/nixos/modules/virtualisation/amazon-image.nix"
      ];
    };
    HP-Envy = nixosSystem {
      system = "x86_64-linux";
      nixosModules = [ ./HP-Envy.nix ];
    };
    HP-ZBook = nixosSystem {
      system = "x86_64-linux";
      nixosModules = [ ./HP-ZBook.nix ];
    };
    MacBook-Pro-Nixos = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./MacBook-Pro-Nixos
        apple-silicon-support.nixosModules.apple-silicon-support
      ];
    };
    nas = nixosSystem {
      system = "x86_64-linux";
      nixosModules = [ ./nas ];
    };
    oracle-cloud-nixos = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./oracle-cloud-nixos.nix
        "${nixpkgs-unstable}/nixos/modules/profiles/qemu-guest.nix"
      ];
    };
    PineBook-Pro = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [
        ./PineBook-Pro.nix
        nixos-hardware.nixosModules.pine64-pinebook-pro
      ];
    };
    Protectli = nixosSystem {
      system = "x86_64-linux";
      nixosModules = [ ./Protectli.nix ];
    };
    router = nixosSystem {
      system = "x86_64-linux";
      nixosModules = [ ./router ];
    };
    rpi4b4a = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [ ./rpi4b4a.nix ];
    };
    rpi4b8a = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [ ./rpi4b8a.nix ];
    };
    rpi4b8b = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [ ./rpi4b8b.nix ];
    };
    rpi4b8c = nixosSystem {
      system = "aarch64-linux";
      nixosModules = [ ./rpi4b8c.nix ];
    };
    steamdeck-nixos = nixosSystem {
      system = "x86_64-linux";
      nixosModules = [
        ./steamdeck-nixos.nix
        jovian.nixosModules.jovian
      ];
    };
    Thinkpad = nixosSystem {
      system = "x86_64-linux";
      nixosModules = [ ./Thinkpad.nix ];
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
