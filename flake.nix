{
  description = "NixOS and nix-darwin configs for my machines";
  inputs = {
    # Nixpkgs
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    nixpkgs-stable.url = "github:nixos/nixpkgs/nixos-24.11";

    # Home manager
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # NixOS profiles to optimize settings for different hardware
    hardware.url = "github:nixos/nixos-hardware";

    # Global catppuccin theme
    catppuccin.url = "github:catppuccin/nix";

    # NixOS Spicetify
    spicetify-nix = {
      url = "github:Gerg-L/spicetify-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Nix Darwin (for MacOS machines)
    darwin = {
      url = "github:LnL7/nix-darwin";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # Homebrew
    nix-homebrew.url = "github:zhaofengli-wip/nix-homebrew";
  };

  outputs = {
    self,
    catppuccin,
    darwin,
    home-manager,
    nix-homebrew,
    nixpkgs,
    flake-utils,
    ...
  } @ inputs: let
    inherit (self) outputs;

    # Systems supported
    allSystems = [
      "x86_64-linux" # 64-bit Intel/AMD Linux
      "aarch64-linux" # 64-bit ARM Linux
      "x86_64-darwin" # 64-bit Intel macOS
      "aarch64-darwin" # 64-bit ARM macOS
    ];

    # A function that provides a system-specific Nixpkgs for the desired systems
    forAllSystems = f: nixpkgs.lib.genAttrs allSystems (system: f {
      pkgs = import nixpkgs { inherit system; };
    });

    # Define user configurations
    users = {
      cdenneen = {
        email = "cdenneen@gmail.com";
        fullName = "Chris Denneen";
        gitKey = "";
        name = "cdenneen";
      };
    };

    # Function for NixOS system configuration
    mkNixosConfiguration = system: username:
      nixpkgs.lib.nixosSystem {
        specialArgs = {
          inherit inputs outputs;
          userConfig = users.${username};
        };
        modules = [
          ./os/nixos.nix
          home-manager.nixosModules.home-manager
        ];
      };

    # Function for nix-darwin system configuration
    mkDarwinConfiguration = system: username:
      darwin.lib.darwinSystem {
        system = system;
        specialArgs = {
          inherit inputs outputs;
          userConfig = users.${username};
        };
        modules = [
          ./os/darwin.nix
          ./home/cdenneen
          home-manager.darwinModules.home-manager
          nix-homebrew.darwinModules.nix-homebrew
        ];
      };

    # Function for Home Manager configuration
    mkHomeConfiguration = system: username:
      home-manager.lib.homeManagerConfiguration {
        pkgs = import nixpkgs {inherit system;};
        extraSpecialArgs = {
          inherit inputs outputs;
          userConfig = users.${username};
        };
        modules = [
          ./home
          ./home/${username}
          catppuccin.homeManagerModules.catppuccin
        ];
      };

  in {
    formatter = forAllSystems (
      system: let
        pkgs = nixpkgs.legacyPackages.${system};
      in
        pkgs.alejandra
    );

    nixosConfigurations = {
      utm = mkNixosConfiguration "utm" "cdenneen";
      eros = mkNixosConfiguration "eros" "cdenneen";
    };

    darwinConfigurations = {
      "cdenneen-mac" = mkDarwinConfiguration "aarch64-darwin" "cdenneen";
      "cdenneen-macintel" = mkDarwinConfiguration "x86_64-darwin" "cdenneen";
    };

    homeConfigurations = {
      "cdenneen@macX86" = mkHomeConfiguration "x86_64-darwin" "cdenneen";
      "cdenneen@mac" = mkHomeConfiguration "aarch64-darwin" "cdenneen";
      "cdenneen@linuxX86" = mkHomeConfiguration "x86_64-linux" "cdenneen";
      "cdenneen@linuxArm" = mkHomeConfiguration "aarch64-linux" "cdenneen";
    };

    overlays = import ./overlays {inherit inputs;};
  };
}
