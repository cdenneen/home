let
  mkHostMap =
    hosts:
    builtins.listToAttrs (
      map (host: {
        name = host.name;
        value = host;
      }) hosts
    );

  nixos = [
    {
      name = "eros";
      system = "aarch64-linux";
      modules = [ ./nixos/eros.nix ];
      homeModules = [ ./nixos/eros-home.nix ];
      tags = [ "ec2" ];
    }
    {
      name = "eros-ec2";
      system = "aarch64-linux";
      modules = [ ./nixos/eros-ec2.nix ];
      tags = [ "ec2" ];
    }
    {
      name = "amazon-ami";
      system = "aarch64-linux";
      modules = [ ./nixos/amazon-ami.nix ];
      tags = [
        "ec2"
        "amazon-ami"
      ];
    }
    {
      name = "nyx";
      system = "aarch64-linux";
      modules = [
        ./nixos/nyx.nix
        ./nixos/nyx-alpha0-node.nix
      ];
      homeModules = [ ./nixos/nyx-home.nix ];
      tags = [ "ec2" ];
    }
    {
      name = "MacBook-Pro-NixOS";
      system = "x86_64-linux";
      modules = [ ./nixos/MacBook-Pro-NixOS.nix ];
      homeModules = [ ./nixos/MacBook-Pro-NixOS-home.nix ];
      tags = [ ];
    }
    {
      name = "ghost";
      system = "aarch64-linux";
      modules = [ ./nixos/ghost.nix ];
      homeModules = [ ./nixos/ghost-home.nix ];
      tags = [ "qemu-guest" ];
    }
    {
      name = "ghost-bootstrap";
      system = "aarch64-linux";
      modules = [ ./nixos/ghost-bootstrap.nix ];
      tags = [ "qemu-guest" ];
    }
    {
      name = "utm";
      system = "aarch64-linux";
      modules = [ ./nixos/utm.nix ];
      tags = [ "qemu-guest" ];
    }
    {
      name = "wsl";
      system = "x86_64-linux";
      modules = [ ./nixos/wsl.nix ];
      homeModules = [ ./nixos/wsl-home.nix ];
      tags = [ "wsl" ];
    }
  ];

  darwin = [
    {
      name = "VNJTECMBCD";
      system = "aarch64-darwin";
      modules = [ ./darwin/VNJTECMBCD.nix ];
      homeModules = [ ./darwin/VNJTECMBCD-home.nix ];
      tags = [ ];
    }
    {
      name = "mbair";
      system = "x86_64-darwin";
      modules = [ ./darwin/mbair.nix ];
      homeModules = [ ./darwin/mbair-home.nix ];
      legacyBigSur = true;
      tags = [ ];
    }
  ];

  all = nixos ++ darwin;
in
{
  inherit nixos darwin all;

  nixosByName = mkHostMap nixos;
  darwinByName = mkHostMap darwin;
  allByName = mkHostMap all;

  hostsByKind = {
    nixos = nixos;
    darwin = darwin;
  };

  hostNames = {
    nixos = map (h: h.name) nixos;
    darwin = map (h: h.name) darwin;
  };
}
