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
    home-manager-mbair = {
      url = "github:nix-community/home-manager/release-25.05";
      inputs.nixpkgs.follows = "nixpkgs-mbair";
    };
    apple-silicon-support.url = "github:nix-community/nixos-apple-silicon";
    axis = {
      url = "git+ssh://git@gitlab.com/ghostspace/axis.git";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nixos-crostini.url = "github:aldur/nixos-crostini";
    mac-app-util.url = "github:hraban/mac-app-util";
    nix-darwin = {
      url = "github:nix-darwin/nix-darwin/nix-darwin-25.11";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-darwin-mbair = {
      url = "github:nix-darwin/nix-darwin/nix-darwin-25.05";
      inputs.nixpkgs.follows = "nixpkgs-mbair";
    };
    nix-index-database = {
      url = "github:nix-community/nix-index-database";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    opnix.url = "github:brizzbuzz/opnix";
    nixos-hardware.url = "github:nixos/nixos-hardware";
    nixos-wsl.url = "github:nix-community/nixos-wsl";
    nixpkgs-stable.follows = "nixpkgs";
    nixpkgs-mbair.url = "github:nixos/nixpkgs/nixos-25.05";
    nixpkgs-unstable.url = "github:nixos/nixpkgs/nixos-unstable";
    opencode-src = {
      url = "github:sst/opencode";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    codex-src = {
      url = "github:sadjow/codex-cli-nix";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    claude-src = {
      url = "github:sadjow/claude-code-nix";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    ponytail-src = {
      url = "github:DietrichGebert/ponytail/16f29800fd2681bdf24f3eb4ccffe38be3baec6b";
      flake = false;
    };
    hermes-src = {
      url = "github:NousResearch/hermes-agent/f5be9236e00ddf2f2a412697f267078fc4ee068e";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    fluxcdAgentSkills = {
      url = "github:cdenneen/fluxcd-agent-skills";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nur.url = "github:nix-community/nur";
    vimnix = {
      url = "github:cdenneen/vimnix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.home-manager.follows = "home-manager";
    };
    nix-homebrew.url = "github:zhaofengli/nix-homebrew";
    nix-homebrew-mbair.url = "github:zhaofengli/nix-homebrew/0406ffd7d3a4e285b618a226a837f4fe9b1a36b7";
    homebrew-taps-mbair = {
      url = "github:cdenneen/homebrew-taps/e70bd4ea4ead556d801227946b9e33131d273466";
      flake = false;
    };
    sops-nix.url = "github:Mic92/sops-nix";
    sops-nix-mbair = {
      url = "github:Mic92/sops-nix/17eea6f3816ba6568b8c81db8a4e6ca438b30b7c";
      inputs.nixpkgs.follows = "nixpkgs-mbair";
    };
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
      import_nixpkgs_with =
        system: nixpkgs:
        {
          darwinMinVersion ? null,
          includeNur ? true,
        }:
        import nixpkgs {
          localSystem = {
            inherit system;
          }
          // nixpkgs.lib.optionalAttrs (darwinMinVersion != null) {
            inherit darwinMinVersion;
          };
          overlays = [
            (final: prev: {
              # nixpkgs kept nixfmt-rfc-style as an alias for a while and emits a warning
              # when it is evaluated. Some tooling (eg treefmt-nix defaults) still refers
              # to that name. Override it to the canonical package to avoid the warning.
              "nixfmt-rfc-style" = prev.nixfmt;

              # Avoid deprecation warning from xorg.lndir alias.
              xorg = prev.xorg // prev.lib.optionalAttrs (prev ? lndir) { lndir = prev.lndir; };

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

              # direnv 2.37.x on darwin builds with `-linkmode=external`,
              # which requires cgo. Force cgo for this package until nixpkgs
              # ships a fixed expression.
              direnv = prev.direnv.overrideAttrs (
                old:
                prev.lib.optionalAttrs prev.stdenv.isDarwin {
                  env = (old.env or { }) // {
                    CGO_ENABLED = "1";
                  };
                }
              );

              # oauth2-proxy 7.15.3 requires Go 1.26, but nixpkgs still uses
              # the default Go 1.25 builder for this package.
              oauth2-proxy =
                if prev.oauth2-proxy.version == "7.15.3" && prev.lib.versionOlder prev.go.version "1.26" then
                  prev.oauth2-proxy.override {
                    buildGoModule = prev.buildGo126Module;
                  }
                else
                  prev.oauth2-proxy;

            })
          ]
          ++ nixpkgs.lib.optional includeNur nur.overlays.default;
          config = {
            allowBroken = true;
            allowUnfree = true;
          };
        };
      import_nixpkgs = system: nixpkgs: import_nixpkgs_with system nixpkgs { };
    in
    flake-parts.lib.mkFlake { inherit inputs; } {
      flake = {
        lib = {
          inherit import_nixpkgs import_nixpkgs_with;
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
        let
          direnvForShell =
            if pkgs.stdenv.isDarwin then
              pkgs.direnv.overrideAttrs (old: {
                env = (old.env or { }) // {
                  CGO_ENABLED = "1";
                };
              })
            else
              pkgs.direnv;
        in
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
              "secrets/ghost.yaml"
              "secrets/axis.yaml"
              "secrets/jarvis.yaml"
            ];

            settings.formatter.prettier.excludes = [
              "secrets/secrets.yaml"
              "secrets/ghost.yaml"
              "secrets/axis.yaml"
              "secrets/jarvis.yaml"
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
            update-workspace-agents = pkgs.callPackage ./pkgs/update-workspace-agents.nix { };
            workspace-init = pkgs.callPackage ./pkgs/workspace-init.nix { };
            setup-repo = pkgs.callPackage ./pkgs/setup-repo.nix { };
            update-workspace = pkgs.callPackage ./pkgs/update-workspace.nix { };
            tokensave = pkgs.callPackage ./pkgs/tokensave.nix { };
            pi-agent = pkgs.callPackage ./pkgs/pi-agent.nix { };
            pi-plugins = pkgs.callPackage ./pkgs/pi-plugins.nix { };
          };

          devshells.default = {
            # Align devshell tooling with system/Home Manager pkgs
            packages = [
              pkgs.git
              pkgs.ripgrep
              direnvForShell
              pkgs.fzf
              pkgs.eza
              self'.packages.treefmt
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

          # Keep flake checks fast and pure. Full host eval/build targets are
          # executed explicitly in CI workflow jobs.
          checks =
            with pkgs.lib;
            let
              isCacheable = v: isDerivation v;
            in
            mapAttrs' (n: nameValuePair "devShells-${n}") (filterAttrs (n: v: isCacheable v) self'.devShells)
            // {
              hermes-supervisor =
                pkgs.runCommand "hermes-supervisor-check"
                  {
                    nativeBuildInputs = [
                      pkgs.check-jsonschema
                      pkgs.git
                      pkgs.pyright
                      (pkgs.python3.withPackages (pythonPackages: [
                        pythonPackages.jsonschema
                        pythonPackages.pytest
                      ]))
                      pkgs.ruff
                    ];
                  }
                  ''
                    cp -R ${./modules/hm/users/cdenneen/hermes-supervisor} source
                    chmod -R u+w source
                    ruff check source/scripts source/tests
                    pytest -q source/tests
                    pyright --project source/pyrightconfig.json
                    check-jsonschema --check-metaschema source/schemas/*.schema.json
                    check-jsonschema --schemafile source/schemas/control.schema.json source/control.defaults.json
                    touch "$out"
                  '';
            };
        };
    };
}
