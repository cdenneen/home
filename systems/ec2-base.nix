{ lib, pkgs, ... }:
{
  # Generic EC2 defaults used for image builds.
  ec2.efi = true;

  # Not useful on EC2.
  # Also avoids an option conflict where fwupd tries to enable udisks2 while
  # the amazon-image profile disables it.
  services.fwupd.enable = lib.mkForce false;

  # Avoid GRUB theme conflicts with the EC2 headless profile.
  catppuccin.grub.enable = lib.mkForce false;

  # Matches running systems (do not change after initial install).
  system.stateVersion = lib.mkDefault "26.05";

  # Keep profiles consistent; host modules may override specifics.
  profiles.defaults.enable = lib.mkDefault true;

  # Generate a per-host AGE key on first boot so sops-nix can decrypt once the
  # public key has been added to the repo recipients.
  systemd.services.sops-age-keygen = {
    description = "Generate host AGE key for sops-nix";
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      set -euo pipefail
      keydir=/var/sops/age
      keyfile=$keydir/keys.txt

      if [ -f "$keyfile" ]; then
        exit 0
      fi

      mkdir -p "$keydir"
      ${pkgs.age}/bin/age-keygen -o "$keyfile"
      chmod 0400 "$keyfile"

      pubkey=$(${pkgs.age}/bin/age-keygen -y "$keyfile" | sed 's/^# public key: //')
      echo "sops-nix host AGE public key: $pubkey"
    '';
  };
}
