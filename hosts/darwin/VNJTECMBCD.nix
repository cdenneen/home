{
  axis,
  pkgs,
  ...
}:
let
  axisRevision = axis.rev or "unknown";
in
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
    axis.packages.${pkgs.stdenv.hostPlatform.system}.axis
    axis.packages.${pkgs.stdenv.hostPlatform.system}.axis-desktop
  ];

  sops.secrets.axis_remote_client_token = {
    sopsFile = ../../secrets/axis.yaml;
    owner = "cdenneen";
    mode = "0400";
  };

  system.activationScripts.axisDeploymentIdentity.text = ''
    /bin/mkdir -p /Users/cdenneen/.local/share/axis
    deployed_at="$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
    cat > /Users/cdenneen/.local/share/axis/deployment-identity.json <<EOF
    {"runtime":"macbookpro","runtime_kind":"axis-desktop","ring":1,"runtime_revision":"${axisRevision}","supervisor_revision":"unknown","deployment_time":"$deployed_at","service_url":"https://ai.denneen.net/api","verification_status":"deployment-recorded","health":"pending-runtime-verification"}
    EOF
    /usr/sbin/chown cdenneen:staff /Users/cdenneen/.local/share/axis/deployment-identity.json
    /bin/chmod 0640 /Users/cdenneen/.local/share/axis/deployment-identity.json
  '';

}
