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

  environment.etc."axis/identity-seed.json".source = "${axis}/examples/first_run/identity-seed.json";

  system.activationScripts.axisDeploymentIdentity.text = ''
    /bin/mkdir -p /Users/cdenneen/.local/share/axis
    if [ ! -f /Users/cdenneen/.local/share/axis/runtime.db ]; then
      ${axis.packages.${pkgs.system}.axis}/bin/axis \
        --data-root /Users/cdenneen/.local/share/axis \
        init \
        --seed-file /etc/axis/identity-seed.json \
        --node-alias Desktop
      ${axis.packages.${pkgs.system}.axis}/bin/axis \
        --data-root /Users/cdenneen/.local/share/axis stop || true
    fi
    deployed_at="$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
    cat > /Users/cdenneen/.local/share/axis/deployment-identity.json <<EOF
    {"runtime":"desktop","ring":1,"runtime_revision":"${axisRevision}","supervisor_revision":"unknown","deployment_time":"$deployed_at","verification_status":"deployment-recorded","health":"pending-runtime-verification"}
    EOF
    /usr/sbin/chown cdenneen:staff /Users/cdenneen/.local/share/axis/deployment-identity.json
    /bin/chmod 0640 /Users/cdenneen/.local/share/axis/deployment-identity.json
  '';

}
