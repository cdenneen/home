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
    # Canonical nixpkgs input required by flake-parts (stable for system builds)
    nixpkgs.url = "github:nixos/nixpkgs/nixos-25.11";
    arion = {
      url = "github:hercules-ci/arion";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    catppuccin.url = "github:catppuccin/nix";
    devshell = {
      url = "github:numtide/devshell";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    flake-parts.url = "github:hercules-ci/flake-parts";
    home-manager = {
      url = "github:nix-community/home-manager/release-25.11";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    apple-silicon-support.url = "github:nix-community/nixos-apple-silicon";
    nixos-crostini.url = "github:aldur/nixos-crostini";
    mac-app-util.url = "github:hraban/mac-app-util";
    nix-darwin = {
      url = "github:nix-darwin/nix-darwin/nix-darwin-25.11";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-index-database = {
      url = "github:nix-community/nix-index-database";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    opnix.url = "github:brizzbuzz/opnix";
    nixos-hardware.url = "github:nixos/nixos-hardware";
    nixos-wsl.url = "github:nix-community/nixos-wsl";
    nixpkgs-stable.url = "github:nixos/nixpkgs/nixos-25.11";
    nixpkgs-unstable.url = "github:nixos/nixpkgs/nixos-unstable";
    opencode.url = "github:anomalyco/opencode/dev";
    nur.url = "github:nix-community/nur";
    vimnix.url = "github:cdenneen/vimnix";
    nix-homebrew.url = "github:zhaofengli/nix-homebrew";
    sops-nix.url = "github:Mic92/sops-nix";
    treefmt-nix.url = "github:numtide/treefmt-nix";
  };

  outputs =
    inputs@{
      apple-silicon-support,
      devshell,
      flake-parts,
      nixos-crostini,
      nur,
      self,
      treefmt-nix,
      ...
    }:
    let
      configurations = import ./systems (inputs // { inherit self; });
      import_nixpkgs =
        system: nixpkgs:
        import nixpkgs {
          inherit system;
          overlays = [
            (final: prev: {
              # nixpkgs kept nixfmt-rfc-style as an alias for a while and emits a warning
              # when it is evaluated. Some tooling (eg treefmt-nix defaults) still refers
              # to that name. Override it to the canonical package to avoid the warning.
              "nixfmt-rfc-style" = prev.nixfmt;

              # Avoid deprecation warning from xorg.lndir alias.
              xorg = prev.xorg // {
                lndir = prev.lndir;
              };

              # inetutils fails on darwin with -Werror=format-security.
              inetutils = prev.inetutils.overrideAttrs (old: {
                NIX_CFLAGS_COMPILE =
                  (old.NIX_CFLAGS_COMPILE or [ ])
                  ++ prev.lib.optionals prev.stdenv.isDarwin [
                    "-Wno-error=format-security"
                    "-Wno-format-security"
                  ];
              });

              # vimnix expects rust-analyzer-nightly; fall back to rust-analyzer.
              rust-analyzer-nightly =
                if prev ? rust-analyzer-nightly then prev.rust-analyzer-nightly else prev.rust-analyzer;
            })
            nur.overlays.default
            # (import ./overlays/opencode.nix) # temporarily disabled; use nixpkgs opencode
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
        nixosModules.default = ./modules/system/nixos;
        darwinModules.default = ./modules/system/darwin;
        homeModules.default = ./modules/hm;
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
              nixfmt = {
                enable = true;
                # Avoid nixpkgs warning about nixfmt-rfc-style aliasing.
                package = pkgs.nixfmt;
              };
              prettier.enable = true;
            };

            # Do not let formatters rewrite encrypted SOPS files.
            settings.global.excludes = [
              "secrets/secrets.yaml"
            ];

            settings.formatter.prettier.excludes = [
              "secrets/secrets.yaml"
            ];
          };

          # Make `nix fmt` work and provide a `treefmt` wrapper.
          formatter = config.treefmt.build.wrapper;

          packages = {
            treefmt = config.treefmt.build.wrapper;
            setup-sops = pkgs.callPackage ./pkgs/setup-sops.nix { };
            setup-git-sops = pkgs.callPackage ./pkgs/setup-git-sops.nix { };
            git-sops = pkgs.callPackage ./pkgs/git-sops.nix { };
            pre-commit = pkgs.callPackage ./pkgs/pre-commit.nix { };
            sops-edit = pkgs.callPackage ./pkgs/sops-edit.nix { };
            sops-update-keys = pkgs.callPackage ./pkgs/sops-update-keys.nix { };
            sops-check = pkgs.callPackage ./pkgs/sops-check.nix { };
            sops-diff-keys = pkgs.callPackage ./pkgs/sops-diff-keys.nix { };
            sops-verify-keys = pkgs.callPackage ./pkgs/sops-verify-keys.nix { };
            sops-bootstrap-host = pkgs.callPackage ./pkgs/sops-bootstrap-host.nix { };
            opencode-telegram-bridge = pkgs.callPackage ./pkgs/opencode-telegram-bridge.nix { };
            update-workspace-agents = pkgs.callPackage ./pkgs/update-workspace-agents.nix { };
            workspace-init = pkgs.callPackage ./pkgs/workspace-init.nix { };
            setup-repo = pkgs.callPackage ./pkgs/setup-repo.nix { };
            update-workspace = pkgs.callPackage ./pkgs/update-workspace.nix { };
          };

          devshells.default = {
            # Align devshell tooling with system/Home Manager pkgs
            packages =
              with pkgs;
              [
                git
                ripgrep
                direnv
                fzf
                eza
                opencode
                codex
              ]
              ++ [ self'.packages.treefmt ];
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
              {
                package = self'.packages.sops-verify-keys;
              }
              {
                package = self'.packages.sops-bootstrap-host;
              }
            ];
            imports = [ "${devshell}/extra/git/hooks.nix" ];
            git.hooks = {
              enable = true;
              pre-commit.text = self'.packages.pre-commit.text;
            };
          };

          checks =
            with pkgs.lib;
            let
              fullChecks = (builtins.getEnv "FULL_CHECKS") == "1";
              isCacheable = v: isDerivation v;
              devShellChecks = mapAttrs' (n: nameValuePair "devShells-${n}") (
                filterAttrs (n: v: isCacheable v) self'.devShells
              );
              homeChecks =
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
                  );
              nixosChecks =
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
                  );
              darwinChecks =
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
            in
            devShellChecks // (if fullChecks then homeChecks // nixosChecks // darwinChecks else { });
        };
    };
}
