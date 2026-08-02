inputs:
let
  self = inputs.self;
  inherit (inputs)
    arion
    catppuccin
    disko
    home-manager
    mac-app-util
    nix-index-database
    nixpkgs
    nixos-crostini
    nixos-wsl
    opnix
    nur
    sops-nix
    ;

  mkPkgs = system: {
    stable = self.lib.import_nixpkgs system inputs.nixpkgs-stable;
    unstable = self.lib.import_nixpkgs system inputs.nixpkgs-unstable;
  };

  mkMbairPkgs =
    system:
    let
      stable = self.lib.import_nixpkgs_with system inputs.nixpkgs-mbair {
        includeNur = false;
      };
    in
    {
      inherit stable;
      # Do not let integrated Home Manager pull current unstable packages into
      # a Big Sur generation.
      unstable = stable;
    };

  mkAgentPkgs = system: {
    claude-code = inputs.claude-src.packages.${system}.claude-code;
    codex = inputs.codex-src.packages.${system}.codex;
    opencode = inputs.opencode-src.packages.${system}.opencode;
    pi = self.packages.${system}.pi-agent;
    pi-plugins = self.packages.${system}.pi-plugins;
  };

  hostCatalog = import ../hosts;

  sharedHomeModulesFor =
    {
      enableCatppuccin ? true,
      enableNixIndex ? true,
      enableNur ? true,
      enableOpnix ? true,
      sopsNix ? sops-nix,
    }:
    [
      (
        if enableCatppuccin then
          catppuccin.homeModules.catppuccin
        else
          ../modules/hm/compat/catppuccin-stub.nix
      )
    ]
    ++ nixpkgs.lib.optional enableNixIndex nix-index-database.homeModules.nix-index
    ++ nixpkgs.lib.optional enableNur nur.modules.homeManager.default
    ++ [ self.homeModules.default ]
    ++ nixpkgs.lib.optional enableOpnix opnix.homeManagerModules.default
    ++ [ sopsNix.homeManagerModules.sops ];

  sharedHomeModules = sharedHomeModulesFor { };

  sharedHomeModulesIntegrated = sharedHomeModules;

  sharedHomeModulesStandalone = sharedHomeModules;

  extraModulesForTags =
    tags:
    let
      has = tag: builtins.elem tag (tags);
    in
    (
      if has "ec2" then
        [
          ../hosts/nixos/ec2-base.nix
          "${nixpkgs}/nixos/modules/virtualisation/amazon-image.nix"
        ]
      else
        [ ]
    )
    ++ (
      if has "amazon-ami" then
        [
          ../hosts/nixos/ec2-base.nix
          "${nixpkgs}/nixos/maintainers/scripts/ec2/amazon-image.nix"
        ]
      else
        [ ]
    )
    ++ (if has "qemu-guest" then [ "${nixpkgs}/nixos/modules/profiles/qemu-guest.nix" ] else [ ])
    ++ (if has "wsl" then [ nixos-wsl.nixosModules.wsl ] else [ ])
    ++ (if has "crostini" then [ nixos-crostini.nixosModules.crostini ] else [ ]);

  mkNixosSystem =
    {
      system,
      nixosModules ? [ ],
      homeModules ? [ ],
      tags ? [ ],
    }:
    let
      pkgsSet = mkPkgs system;
      stablePkgs = pkgsSet.stable;
      unstablePkgs = pkgsSet.unstable;
      agentPkgs = mkAgentPkgs system;
      homeStateVersion = "25.11";
      specialArgs = inputs // {
        inherit
          system
          stablePkgs
          unstablePkgs
          agentPkgs
          homeStateVersion
          ;
      };
    in
    nixpkgs.lib.nixosSystem {
      inherit system;
      pkgs = stablePkgs;
      specialArgs = specialArgs;
      modules = [
        ../modules/shared/users/cdenneen.nix
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
            sharedModules = sharedHomeModulesIntegrated;
          };
        }
        {
          home-manager.users.cdenneen.imports = homeModules;
        }
      ]
      ++ extraModulesForTags tags
      ++ nixosModules;
    };

  mkDarwinSystem =
    {
      system,
      darwinModules ? [ ],
      homeModules ? [ ],
      legacyBigSur ? false,
    }:
    let
      pkgsSet = if legacyBigSur then mkMbairPkgs system else mkPkgs system;
      stablePkgs = pkgsSet.stable;
      unstablePkgs = pkgsSet.unstable;
      agentPkgs = if legacyBigSur then null else mkAgentPkgs system;
      homeManagerInput = if legacyBigSur then inputs.home-manager-mbair else home-manager;
      nixDarwinInput = if legacyBigSur then inputs.nix-darwin-mbair else inputs.nix-darwin;
      nixHomebrewInput = if legacyBigSur then inputs.nix-homebrew-mbair else inputs.nix-homebrew;
      sopsNixInput = if legacyBigSur then inputs.sops-nix-mbair else sops-nix;
      homeStateVersion = if legacyBigSur then "25.05" else "25.11";
      hostHomeModules = sharedHomeModulesFor {
        enableCatppuccin = !legacyBigSur;
        enableNixIndex = !legacyBigSur;
        enableNur = !legacyBigSur;
        enableOpnix = !legacyBigSur;
        sopsNix = sopsNixInput;
      };
      specialArgs = inputs // {
        inherit
          system
          stablePkgs
          unstablePkgs
          agentPkgs
          homeStateVersion
          ;
      };
    in
    assert nixpkgs.lib.assertMsg (
      !legacyBigSur || system == "x86_64-darwin"
    ) "mkDarwinSystem: legacyBigSur requires system = x86_64-darwin";
    nixDarwinInput.lib.darwinSystem {
      pkgs = stablePkgs;
      specialArgs = specialArgs;
      modules = [
        ../modules/shared/users/cdenneen.nix
        homeManagerInput.darwinModules.default
        nixHomebrewInput.darwinModules.nix-homebrew
      ]
      ++ nixpkgs.lib.optionals (!legacyBigSur) [
        mac-app-util.darwinModules.default
        nix-index-database.darwinModules.nix-index
        nur.modules.darwin.default
      ]
      ++ [
        self.darwinModules.default
        sopsNixInput.darwinModules.sops
        {
          home-manager = {
            extraSpecialArgs = specialArgs;
            sharedModules =
              nixpkgs.lib.optionals (!legacyBigSur) [
                mac-app-util.homeManagerModules.default
              ]
              ++ hostHomeModules;
          };
          homebrew = {
            enable = true;
            user = "cdenneen";
          };
        }
        (nixpkgs.lib.mkIf legacyBigSur {
          home-manager.useGlobalPkgs = nixpkgs.lib.mkForce true;
        })
        {
          home-manager.users.cdenneen.imports = homeModules;
        }
      ]
      ++ darwinModules;
    };

  mkHomeConfiguration =
    {
      system,
      homeModules ? [ ],
      legacyBigSur ? false,
    }:
    let
      pkgsSet = if legacyBigSur then mkMbairPkgs system else mkPkgs system;
      stablePkgs = pkgsSet.stable;
      unstablePkgs = pkgsSet.unstable;
      agentPkgs = if legacyBigSur then null else mkAgentPkgs system;
      homeManagerInput = if legacyBigSur then inputs.home-manager-mbair else home-manager;
      sopsNixInput = if legacyBigSur then inputs.sops-nix-mbair else sops-nix;
      homeStateVersion = if legacyBigSur then "25.05" else "25.11";
      hostHomeModules = sharedHomeModulesFor {
        enableCatppuccin = !legacyBigSur;
        enableNixIndex = !legacyBigSur;
        enableNur = !legacyBigSur;
        enableOpnix = !legacyBigSur;
        sopsNix = sopsNixInput;
      };
    in
    assert nixpkgs.lib.assertMsg (
      !legacyBigSur || system == "x86_64-darwin"
    ) "mkHomeConfiguration: legacyBigSur requires system = x86_64-darwin";
    homeManagerInput.lib.homeManagerConfiguration {
      pkgs = unstablePkgs;
      extraSpecialArgs = inputs // {
        inherit
          system
          stablePkgs
          unstablePkgs
          agentPkgs
          homeStateVersion
          ;
      };
      modules = homeModules ++ hostHomeModules;
    };

  lib = {
    inherit
      mkPkgs
      mkMbairPkgs
      mkAgentPkgs
      mkNixosSystem
      mkDarwinSystem
      mkHomeConfiguration
      sharedHomeModules
      sharedHomeModulesFor
      sharedHomeModulesIntegrated
      sharedHomeModulesStandalone
      hostCatalog
      extraModulesForTags
      ;

    bootstrap =
      {
        hostName,
        system,
        kind ? "nixos",
        tags ? [ ],
        nixosModules ? [ ],
        darwinModules ? [ ],
        homeModules ? [ ],
        legacyBigSur ? false,
      }:
      let
        defaultHomeModule =
          { pkgs, ... }:
          {
            _module.args.nixHostName = hostName;
            home.username = "cdenneen";
            home.homeDirectory = if pkgs.stdenv.isDarwin then "/Users/cdenneen" else "/home/cdenneen";
            profiles.defaults.enable = true;
            profiles.gui.enable = pkgs.stdenv.isDarwin;
          };

        homeConfigurations = {
          cdenneen = mkHomeConfiguration {
            inherit system legacyBigSur;
            homeModules = [ defaultHomeModule ] ++ homeModules;
          };
        };

        nixosConfigurations = {
          ${hostName} = mkNixosSystem {
            inherit
              system
              tags
              homeModules
              ;
            nixosModules = [
              (
                { ... }:
                {
                  networking.hostName = hostName;
                }
              )
            ]
            ++ nixosModules;
          };
        };

        darwinConfigurations = {
          ${hostName} = mkDarwinSystem {
            inherit
              system
              homeModules
              legacyBigSur
              ;
            darwinModules = [
              (
                { ... }:
                {
                  networking.hostName = hostName;
                }
              )
            ]
            ++ darwinModules;
          };
        };
      in
      {
        inherit homeConfigurations;
        nixosConfigurations = if kind == "nixos" then nixosConfigurations else { };
        darwinConfigurations = if kind == "darwin" then darwinConfigurations else { };
      };
  };

  nixos = import ./nixos.nix {
    inherit
      inputs
      self
      lib
      hostCatalog
      ;
  };
  darwin = import ./darwin.nix {
    inherit
      inputs
      self
      lib
      hostCatalog
      ;
  };
  home = import ./home.nix {
    inherit
      inputs
      self
      lib
      hostCatalog
      ;
  };
in
{
  lib = lib // {
    inherit (darwin) darwinSystem;
    inherit (home) homeConfiguration;
    inherit (nixos) nixosSystem;
  };

  inherit (darwin) darwinConfigurations;
  inherit (home) homeConfigurations;
  inherit (nixos) nixosConfigurations;
  nixosConfigurationsAll = nixos.allNixosConfigurations;
}
