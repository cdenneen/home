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

  sharedHomeModulesIntegrated = [
    catppuccin.homeModules.catppuccin
    nix-index-database.homeModules.nix-index
    nur.modules.homeManager.default
    self.homeModules.default
    opnix.homeManagerModules.default
    sops-nix.homeManagerModules.sops
  ];

  sharedHomeModulesStandalone = [
    catppuccin.homeModules.catppuccin
    nix-index-database.homeModules.nix-index
    nur.modules.homeManager.default
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
      opencodeOverride =
        if system == "aarch64-linux" then
          let
            opencodeSrc = inputs.opencode.outPath;
            opencodeRev = inputs.opencode.shortRev or inputs.opencode.dirtyShortRev or "dirty";
            node_modules = stablePkgs.callPackage "${opencodeSrc}/nix/node_modules.nix" {
              rev = opencodeRev;
              hash = "sha256-xWp4LLJrbrCPFL1F6SSbProq/t/az4CqhTcymPvjOBQ=";
            };
            opencodePkg = stablePkgs.callPackage "${opencodeSrc}/nix/opencode.nix" {
              inherit node_modules;
            };
          in
          inputs.opencode
          // {
            packages = inputs.opencode.packages // {
              ${system} = inputs.opencode.packages.${system} // {
                default = opencodePkg;
                opencode = opencodePkg;
              };
            };
          }
        else
          inputs.opencode;
      specialArgs = inputs // {
        inherit system stablePkgs unstablePkgs;
        opencode = opencodeOverride;
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
            sharedModules = homeModules ++ sharedHomeModulesIntegrated;
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
        mac-app-util.darwinModules.default
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
      modules = homeModules ++ sharedHomeModulesStandalone;
    };

  lib = {
    inherit
      mkPkgs
      mkNixosSystem
      mkDarwinSystem
      mkHomeConfiguration
      sharedHomeModulesIntegrated
      sharedHomeModulesStandalone
      extraModulesForTags
      ;

    bootstrap =
      {
        hostName,
        system,
        kind ? "nixos",
        tags ? [ ],
        users ? [ "cdenneen" ],
        nixosModules ? [ ],
        darwinModules ? [ ],
        homeModules ? [ ],
      }:
      let
        defaultHomeModule =
          username:
          { pkgs, ... }:
          {
            home.username = username;
            home.homeDirectory = if pkgs.stdenv.isDarwin then "/Users/${username}" else "/home/${username}";
            profiles.defaults.enable = true;
            profiles.gui.enable = pkgs.stdenv.isDarwin;
          };

        homeConfigurations = builtins.listToAttrs (
          map (username: {
            name = username;
            value = mkHomeConfiguration {
              inherit system;
              homeModules = [ (defaultHomeModule username) ] ++ homeModules;
            };
          }) users
        );

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

  nixos = import ./nixos.nix { inherit inputs self lib; };
  darwin = import ./darwin.nix { inherit inputs self lib; };
  home = import ./home.nix { inherit inputs self lib; };
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
