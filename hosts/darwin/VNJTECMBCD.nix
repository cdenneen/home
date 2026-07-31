{
  pkgs,
  ...
}:
{

  networking.hostName = "VNJTECMBCD";

  system.stateVersion = 6;
  system.primaryUser = "cdenneen";

  environment.systemPackages = [
    pkgs.bash
    pkgs.nodejs_24
    pkgs.pnpm
    pkgs.podman
    pkgs.uv
  ];

}
