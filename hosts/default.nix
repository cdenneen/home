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
      modules = [ ./nixos/nyx.nix ];
      tags = [ "ec2" ];
    }
    {
      name = "MacBook-Pro-NixOS";
      system = "x86_64-linux";
      modules = [ ./nixos/MacBook-Pro-NixOS.nix ];
      tags = [ ];
    }
    {
      name = "ghost";
      system = "aarch64-linux";
      modules = [ ./nixos/ghost.nix ];
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
      tags = [ "wsl" ];
    }
  ];

  darwin = [
    {
      name = "VNJTECMBCD";
      system = "aarch64-darwin";
      modules = [ ./darwin/VNJTECMBCD.nix ];
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
