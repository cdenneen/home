let
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
      name = "oracle-cloud-nixos";
      system = "aarch64-linux";
      modules = [ ./nixos/oracle-cloud-nixos.nix ];
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
in
{
  inherit nixos darwin;

  hostsByKind = {
    nixos = nixos;
    darwin = darwin;
  };

  hostNames = {
    nixos = map (h: h.name) nixos;
    darwin = map (h: h.name) darwin;
  };
}
