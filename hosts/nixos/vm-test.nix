# Workdir extension of disposable integration test. Approved package baseline unchanged.
#   nix build -L -f vm-test.nix
{
  pkgs ? import <nixpkgs> { },
}:
pkgs.testers.runNixOSTest {
  name = "axis-acceptance-runner";
  nodes.machine = { ... }: {
    imports = [ ./platform-axis-acceptance.nix ];
    users.groups.axis = { };
    users.users.axis = {
      isSystemUser = true;
      group = "axis";
      home = "/var/lib/axis";
      createHome = true;
    };
    environment.systemPackages = [
      pkgs.jq
      pkgs.python312.out
      pkgs.util-linux
    ];
    documentation.nixos.enable = false;
    documentation.man.enable = false;
    documentation.doc.enable = false;
    documentation.info.enable = false;
    virtualisation.memorySize = 2048;
    virtualisation.cores = 2;
  };

  testScript = builtins.readFile ./vm-test-script.py;
}
