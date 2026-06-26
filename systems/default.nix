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
    sops-nix
    ;

  mkPkgs = system: {
    stable = self.lib.import_nixpkgs system inputs.nixpkgs-stable;
    unstable = self.lib.import_nixpkgs system inputs.nixpkgs-unstable;
  };

  hostCatalog = import ../hosts;

  sharedHomeModulesFor = system: [
    (
      if system == "x86_64-darwin" then
        ../modules/hm/compat/catppuccin-stub.nix
      else
        catppuccin.homeModules.catppuccin
    )
    nix-index-database.homeModules.nix-index
    self.homeModules.default
    opnix.homeManagerModules.default
    sops-nix.homeManagerModules.sops
  ];

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
      specialArgs = inputs // {
        inherit system stablePkgs unstablePkgs;
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
        self.nixosModules.default
        sops-nix.nixosModules.sops
        {
          home-manager = {
            extraSpecialArgs = specialArgs;
            sharedModules = homeModules ++ sharedHomeModulesFor system;
          };
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
    }:
    let
      pkgsSet = mkPkgs system;
      stablePkgs = pkgsSet.stable;
      unstablePkgs = pkgsSet.unstable;
      specialArgs = inputs // {
        inherit system stablePkgs unstablePkgs;
      };
    in
    inputs.nix-darwin.lib.darwinSystem {
      pkgs = stablePkgs;
      specialArgs = specialArgs;
      modules = [
        ../modules/shared/users/cdenneen.nix
        home-manager.darwinModules.default
        inputs.nix-homebrew.darwinModules.nix-homebrew
        nix-index-database.darwinModules.nix-index
        self.darwinModules.default
        sops-nix.darwinModules.sops
      ]
      ++ nixpkgs.lib.optionals (system != "x86_64-darwin") [
        mac-app-util.darwinModules.default
      ]
      ++ [
        {
          home-manager = {
            extraSpecialArgs = specialArgs;
            sharedModules =
              nixpkgs.lib.optionals (system != "x86_64-darwin") [
                mac-app-util.homeManagerModules.default
              ]
              ++ homeModules
              ++ sharedHomeModulesFor system;
          };
          homebrew = {
            enable = true;
            user = "cdenneen";
          };
        }
      ]
      ++ darwinModules;
    };

  mkHomeConfiguration =
    {
      system,
      homeModules ? [ ],
    }:
    let
      pkgsSet = mkPkgs system;
      stablePkgs = pkgsSet.stable;
      unstablePkgs = pkgsSet.unstable;
    in
    home-manager.lib.homeManagerConfiguration {
      pkgs = unstablePkgs;
      extraSpecialArgs = inputs // {
        inherit system stablePkgs unstablePkgs;
      };
      modules = homeModules ++ sharedHomeModulesFor system;
    };

  lib = {
    inherit
      mkPkgs
      mkNixosSystem
      mkDarwinSystem
      mkHomeConfiguration
      sharedHomeModulesFor
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
      }:
      let
        defaultHomeModule =
          { pkgs, ... }:
          {
            home.username = "cdenneen";
            home.homeDirectory = if pkgs.stdenv.isDarwin then "/Users/cdenneen" else "/home/cdenneen";
            profiles.defaults.enable = true;
            profiles.gui.enable = pkgs.stdenv.isDarwin;
          };

        homeConfigurations = {
          cdenneen = mkHomeConfiguration {
            inherit system;
            homeModules = [ defaultHomeModule ] ++ homeModules;
          };
        };

        nixosConfigurations = {
          ${hostName} = mkNixosSystem {
            inherit system tags;
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
            inherit system;
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
