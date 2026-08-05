{
  axis,
  pkgs,
  ...
}:
let
  axisRevision = axis.rev or "unknown";
in
{
  imports = [ axis.darwinModules.default ];

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

  services.axis = {
    enable = true;
    dataRoot = "/Users/cdenneen/.local/share/axis";
    user = "cdenneen";
    host = "127.0.0.1";
    port = 8780;
  };

  system.activationScripts.axisDeploymentIdentity.text = ''
    /bin/mkdir -p /Users/cdenneen/.local/share/axis
    deployed_at="$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
    cat > /Users/cdenneen/.local/share/axis/deployment-identity.json <<EOF
    {"runtime":"desktop","ring":1,"runtime_revision":"${axisRevision}","supervisor_revision":"unknown","deployment_time":"$deployed_at","verification_status":"deployment-recorded","health":"pending-runtime-verification"}
    EOF
    /usr/sbin/chown cdenneen:staff /Users/cdenneen/.local/share/axis/deployment-identity.json
    /bin/chmod 0640 /Users/cdenneen/.local/share/axis/deployment-identity.json
  '';

}
