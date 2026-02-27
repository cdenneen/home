{
  config,
  lib,
  ...
}:
{
  sops.secrets = { };

  programs.starship.settings.palette = lib.mkForce "nyx";
}
