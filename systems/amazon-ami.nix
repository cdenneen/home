{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.amazonImage;
  amiBootMode = if config.ec2.efi then "uefi" else "legacy-bios";

  proxyFlake = pkgs.writeText "flake.nix" ''
    {
      description = "Proxy flake for EC2 instances";

      inputs.upstream.url = "github:ToyVo/nixcfg";

      outputs = { upstream, ... }: upstream.outputs;
    }
  '';

  proxyReadme = pkgs.writeText "README.txt" ''
    /etc/nixos/flake.nix is a proxy to github:ToyVo/nixcfg.

    Examples:
      sudo nixos-rebuild switch --flake /etc/nixos#<host>
  '';

  # Copied from nixpkgs' amazon-image.nix, but with a larger ESP.
  configFile = pkgs.writeText "configuration.nix" ''
    { modulesPath, ... }: {
      imports = [ "''${modulesPath}/virtualisation/amazon-image.nix" ];
      ${lib.optionalString config.ec2.efi ''
        ec2.efi = true;
      ''}
      ${lib.optionalString config.ec2.zfs.enable ''
        ec2.zfs.enable = true;
        networking.hostId = "${config.networking.hostId}";
      ''}
    }
  '';

  amazonImage750mEsp = import "${pkgs.path}/nixos/lib/make-disk-image.nix" {
    inherit
      lib
      config
      pkgs
      configFile
      ;

    inherit (cfg) contents format;
    inherit (config.image) baseName;
    name = config.image.baseName;

    fsType = "ext4";
    partitionTableType = if config.ec2.efi then "efi" else "legacy+gpt";
    inherit (config.virtualisation) diskSize;

    bootSize = "750M";

    postVM = ''
      mkdir -p $out/nix-support
      echo "file ${cfg.format} $diskImage" >> $out/nix-support/hydra-build-products

      ${pkgs.jq}/bin/jq -n \
        --arg system_version ${lib.escapeShellArg config.system.nixos.version} \
        --arg system ${lib.escapeShellArg pkgs.stdenv.hostPlatform.system} \
        --arg logical_bytes "$(${pkgs.qemu_kvm}/bin/qemu-img info --output json \"$diskImage\" | ${pkgs.jq}/bin/jq '."virtual-size"')" \
        --arg boot_mode "${amiBootMode}" \
        --arg file "$diskImage" \
        '{}
          | .label = $system_version
          | .boot_mode = $boot_mode
          | .system = $system
          | .logical_bytes = $logical_bytes
          | .file = $file
          | .disks.root.logical_bytes = $logical_bytes
          | .disks.root.file = $file
          ' > $out/nix-support/image-info.json
    '';
  };
in
{
  # Prefer a stable base name for artifacts.
  image.baseName = lib.mkDefault "amazon-ami";
  amazonImage.format = lib.mkDefault "vpc";

  # Provide a proxy flake so the instance can rebuild without cloning.
  amazonImage.contents = lib.mkAfter [
    {
      source = proxyFlake;
      target = "etc/nixos/flake.nix";
      mode = "0644";
    }
    {
      source = proxyReadme;
      target = "etc/nixos/README.txt";
      mode = "0644";
    }
  ];

  # Override the image builder to use a larger ESP (default is 256M).
  system.build.amazonImage = lib.mkForce amazonImage750mEsp;
}
