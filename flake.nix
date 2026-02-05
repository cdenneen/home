{
  description = "Collin Diekvoss Nix Configurations";

  nixConfig = {
    trusted-users = [
      "root"
      "cdenneen"
    ];
    extra-substituters = [
      "https://cache.nixos.org"
      "https://nix-community.cachix.org"
      "https://cdenneen.cachix.org"
    ];
    extra-trusted-public-keys = [
      "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
      "nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs="
      "cdenneen.cachix.org-1:EUognwSf1y0FAzDOPmUuYtz6aOxCWyNbcMi8PjHV8gU="
    ];
  };

  inputs = {
    # Canonical nixpkgs input required by flake-parts
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    apple-silicon-support.url = "github:tpwrules/nixos-apple-silicon";
    arion = {
      url = "github:hercules-ci/arion";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    catppuccin.url = "github:catppuccin/nix";
    devshell = {
      url = "github:numtide/devshell";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    discord_bot.url = "github:toyvo/discord_bot";
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    flake-parts.url = "github:hercules-ci/flake-parts";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    jovian.url = "github:Jovian-Experiments/Jovian-NixOS";
    mac-app-util.url = "github:hraban/mac-app-util";
    nh.url = "github:toyvo/nh";
    nix-darwin = {
      url = "github:nix-darwin/nix-darwin";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    nix-index-database = {
      url = "github:nix-community/nix-index-database";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    nixos-hardware.url = "github:nixos/nixos-hardware";
    nixos-wsl.url = "github:nix-community/nixos-wsl";
    nixpkgs-esp-dev.url = "github:mirrexagon/nixpkgs-esp-dev";
    nixpkgs-stable.url = "github:nixos/nixpkgs/nixos-25.11";
    nixpkgs-unstable.follows = "nixpkgs";
    nur-packages.url = "github:ToyVo/nur-packages";
    nur.url = "github:nix-community/nur";
    nvf.url = "github:NotAShelf/nvf";
    plasma-manager.url = "github:pjones/plasma-manager";
    nix-homebrew.url = "github:zhaofengli/nix-homebrew";
    rust-overlay.url = "github:oxalica/rust-overlay";
    sops-nix.url = "github:Mic92/sops-nix";
    treefmt-nix.url = "github:numtide/treefmt-nix";
    zed.url = "github:zed-industries/zed";
  };

  outputs =
    inputs@{
      devshell,
      flake-parts,
      nixpkgs-esp-dev,
      nixpkgs-unstable,
      nur,
      nur-packages,
      rust-overlay,
      self,
      treefmt-nix,
      zed,
      ...
    }:
    let
      configurations = import ./systems inputs;
      import_nixpkgs =
        system: nixpkgs:
        import nixpkgs {
          inherit system;
          overlays = [
            nixpkgs-esp-dev.overlays.default
            nur-packages.overlays.default
            nur.overlays.default
            rust-overlay.overlays.default
            # (import ./overlays/opencode.nix) # temporarily disabled; use nixpkgs opencode
            # zed.overlays.default
          ];
          config = {
            allowBroken = true;
            allowUnfree = true;
          };
        };
    in
    flake-parts.lib.mkFlake { inherit inputs; } {
      flake = {
        lib = {
          inherit import_nixpkgs;
        }
        // configurations.lib;
        nixosModules.default = ./modules/nixos;
        darwinModules.default = ./modules/darwin;
        commonModules = {
          users = {
            cdenneen = ./modules/common/users/cdenneen.nix;
          };
        };
        homeModules.default = ./modules/home;
        nixosConfigurations = configurations.nixosConfigurations;
        darwinConfigurations = configurations.darwinConfigurations;
        homeConfigurations = configurations.homeConfigurations;
      };
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      imports = [
        devshell.flakeModule
        flake-parts.flakeModules.easyOverlay
        treefmt-nix.flakeModule
      ];
      perSystem =
        {
          config,
          pkgs,
          lib,
          system,
          self',
          ...
        }:
        {
          # Let flake-parts provide pkgs; do not override to avoid recursion
          # Let flake-parts manage pkgs; do not override with a manual nixpkgs import

          treefmt = {
            programs = {
              nixfmt.enable = true;
              prettier.enable = true;
            };
          };

          packages = {
            setup-sops = pkgs.callPackage ./pkgs/setup-sops.nix { };
            setup-git-sops = pkgs.callPackage ./pkgs/setup-git-sops.nix { };
            git-sops = pkgs.callPackage ./pkgs/git-sops.nix { };
            pre-commit = pkgs.callPackage ./pkgs/pre-commit.nix { };
            sops-edit = pkgs.callPackage ./pkgs/sops-edit.nix { };
            sops-update-keys = pkgs.callPackage ./pkgs/sops-update-keys.nix { };
            sops-check = pkgs.callPackage ./pkgs/sops-check.nix { };
            sops-diff-keys = pkgs.callPackage ./pkgs/sops-diff-keys.nix { };
          };

          devshells.default = {
            # Align devshell tooling with system/Home Manager pkgs
            packages = with pkgs; [
              git
              atuin
              zoxide
              opencode
            ];
            commands = [
              {
                package = self'.packages.setup-sops;
              }
              {
                package = self'.packages.setup-git-sops;
              }
              {
                package = self'.packages.sops-edit;
              }
              {
                package = self'.packages.sops-update-keys;
              }
              {
                package = self'.packages.sops-check;
              }
              {
                package = self'.packages.sops-diff-keys;
              }
            ];
            imports = [ "${devshell}/extra/git/hooks.nix" ];
            git.hooks = {
              enable = true;
              pre-commit.text = self'.packages.pre-commit.text;
            };
          };

          checks =
            with nixpkgs-unstable.lib;
            with nur-packages.lib;
            flakeChecks system self'.packages
            // mapAttrs' (n: nameValuePair "devShells-${n}") (filterAttrs (n: v: isCacheable v) self'.devShells)
            //
              mapAttrs'
                (
                  n: v:
                  (nameValuePair "homeConfigurations-${n}") (
                    self.homeConfigurations."${n}".config.home.activationPackage
                  )
                )
                (
                  filterAttrs (
                    n: v: self.homeConfigurations."${n}".pkgs.stdenv.system == system
                  ) self.homeConfigurations
                )
            //
              mapAttrs'
                (
                  n: v:
                  (nameValuePair "nixosConfigurations-${n}") (
                    self.nixosConfigurations."${n}".config.system.build.toplevel
                  )
                )
                (
                  filterAttrs (
                    n: v: self.nixosConfigurations."${n}".pkgs.stdenv.system == system
                  ) self.nixosConfigurations
                )
            //
              mapAttrs'
                (
                  n: v:
                  (nameValuePair "darwinConfigurations-${n}") (
                    self.darwinConfigurations."${n}".config.system.build.toplevel
                  )
                )
                (
                  filterAttrs (
                    n: v: self.darwinConfigurations."${n}".pkgs.stdenv.system == system
                  ) self.darwinConfigurations
                );
        };
    };
}
