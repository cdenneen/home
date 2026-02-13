{
  lib,
  pkgs,
  config,
  ...
}:
{
  networking.hostName = "nyx";
  ec2.efi = true;

  services.tailscale = {
    enable = true;
    openFirewall = true;
  };

  # Allow inbound services over the private tailnet without opening ports to the public internet.
  networking.firewall.trustedInterfaces = lib.mkAfter [ "tailscale0" ];

  # Convenience for ad-hoc HTTP services during debugging.
  networking.firewall.interfaces.tailscale0.allowedTCPPorts = [ 8080 ];

  programs.mosh.enable = true;
  networking.firewall.allowedUDPPortRanges = lib.mkAfter [
    {
      from = 60000;
      to = 61000;
    }
  ];

  fileSystems."/home/cdenneen/src" = {
    device = "UUID=48a9e4a3-252f-4676-afd9-f2ed39ac8e90";
    fsType = "ext4";
    options = [ "noatime" ];
  };

  # Keep the system bootable on EC2 (UEFI GRUB on ESP at /boot).
  boot.loader.grub.configurationLimit = 3;

  # Avoid conflicts with the EC2 headless profile's GRUB defaults.
  catppuccin.grub.enable = lib.mkForce false;

  # Switch display manager from Plasma to XFCE
  services.desktopManager.plasma6.enable = false;
  services.xserver.desktopManager.xfce.enable = true;
  services.xserver.displayManager.lightdm.enable = true;
  services.displayManager.sddm.enable = false;

  services.udisks2.enable = lib.mkForce false;
  services.openssh.settings.PermitRootLogin = lib.mkForce "prohibit-password";

  # Matches running system (do not change after initial install)
  system.stateVersion = lib.mkForce "26.05";

  # User definition is shared via commonModules.users.cdenneen
  profiles.defaults.enable = true;

  # Let user systemd services start at boot (no login needed).
  users.users.cdenneen.linger = true;

  # Cloudflare Tunnel for Telegram webhook.
  environment.systemPackages = lib.mkAfter [ pkgs.cloudflared ];
  sops.secrets.cloudflare_tunnel_token = {
    mode = "0400";
    restartUnits = [ "cloudflared-telegram-bridge.service" ];
  };
  systemd.services.cloudflared-telegram-bridge =
    let
      run = pkgs.writeShellScript "cloudflared-telegram-bridge" ''
        set -euo pipefail
        token_file="${config.sops.secrets.cloudflare_tunnel_token.path}"
        exec ${pkgs.cloudflared}/bin/cloudflared tunnel run --token "$(cat "$token_file")"
      '';
    in
    {
      description = "Cloudflare Tunnel (Telegram bridge)";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        ExecStart = run;
        Restart = "always";
        RestartSec = 2;
      };
    };

  home-manager.users.cdenneen.opencodeTelegramBridge = {
    updatesMode = "webhook";
    webhookPublicUrl = "https://nyx.denneen.net";
  };
}
