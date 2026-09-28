# Proposed addition to github.com/cdenneen/home: hosts/nixos/ghost.nix (imported as a module).
# Base: origin/main a948a6dbb5f7f76a3043fc01c394f9032ebed2e0. DRAFT - not merged, not activated.
{ config, lib, pkgs, ... }:
let
  spool = "/var/lib/axis-acceptance";
  python = pkgs.python312;               # interpreter is platform-owned, not chosen by AXIS or the candidate
  runnerVersion = "axis.acceptance-runner.v1";

  # Fixed, scenario-agnostic entry point. It never interprets job data as shell: the job's
  # module name and argument vector are read as JSON by a platform-owned Python stub, validated
  # against a strict pattern, and passed to execv as a list. No scenario name is baked in here,
  # so new scenarios need no host change.
  entryPy = pkgs.writeText "axis-acceptance-entry.py" ''
    import json, os, re, sys
    job_id = sys.argv[1]
    if not re.fullmatch(r"[a-z0-9-]{8,64}", job_id):
        sys.exit(64)
    job = "${spool}/jobs/" + job_id
    if not os.path.exists(job + "/SEALED"):
        sys.exit(65)
    with open(job + "/case.json") as fh:
        case = json.load(fh)
    module = case["module"]
    argv = case["argv"]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", module):
        sys.exit(66)
    if not (isinstance(argv, list) and len(argv) <= 32
            and all(isinstance(a, str) and len(a) <= 256 and "\\0" not in a for a in argv)):
        sys.exit(67)
    os.chdir(job + "/snapshot/src")
    py = "${python}/bin/python3"
    os.execv(py, [py, "-E", "-s", "-B", "-m", module, *argv])
  '';
  # Seal/retire implementation. Kept platform-owned so the tested bytes are fixed by root.
  sealPy = pkgs.writeText "axis-acceptance-seal.py" (builtins.readFile ./axis-acceptance-seal.py);

  entry = pkgs.writeShellScript "axis-acceptance-entry" ''
    exec ${python}/bin/python3 -E -s -B ${entryPy} "$1"
  '';

  # Published contract. Includes hashes of the exact template and entry so AXIS can check
  # it is talking to this implementation, not just a matching version string.
  contract = pkgs.writeText "axis-acceptance-runner.json" (builtins.toJSON {
    interface = runnerVersion;
    spool = spool;
    unit_template = "axis-acceptance@.service";
    entry = "${entry}";
    interpreter = "${python}/bin/python3";
    max_runtime_s = 30;
    max_tasks = 32;
    max_memory = "256M";
  });
in
{
  systemd.tmpfiles.rules = [
    # root owns the spool; axis may create job dirs inside incoming/, nothing else.
    "d ${spool}          0755 root root -"
    "d ${spool}/incoming 0730 root axis -"
    "d ${spool}/jobs     0755 root root -"
  ];

  environment.etc."axis/acceptance-runner.json".source = contract;

  systemd.services."axis-acceptance@" = {
    description = "AXIS bounded acceptance case %i";
    serviceConfig = {
      Type = "oneshot";
      # RuntimeMaxSec has no effect with Type=oneshot; TimeoutStartSec bounds the case.
      TimeoutStartSec = 30;
      ExecStart = "${entry} %i";

      DynamicUser = true;
      BindReadOnlyPaths = [ "${spool}/jobs/%i" ];
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      PrivateDevices = true;
      PrivateIPC = true;
      PrivateNetwork = true;
      ProtectProc = "invisible";
      ProcSubset = "pid";
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      RestrictSUIDSGID = true;
      RestrictNamespaces = true;
      LockPersonality = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      SystemCallFilter = [ "@system-service" ];

      KillMode = "control-group";
      RuntimeMaxSec = 30;
      TasksMax = 32;
      MemoryMax = "256M";

      StandardOutput = "file:${spool}/jobs/%i/result/stdout";   # see LIFECYCLE: result/ is the one writable path
      StandardError  = "file:${spool}/jobs/%i/result/stderr";
    };
  };

  # Root-owned seal step. Moves a staged job out of axis's reach, makes it root-owned and
  # read-only, recomputes the tree digest itself, and writes SEALED. Its stop action retires the job.
  systemd.services."axis-acceptance-seal@" = {
    description = "Seal AXIS acceptance job %i";
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${python}/bin/python3 -E -s -B ${sealPy} seal %i";
      ExecStop  = "${python}/bin/python3 -E -s -B ${sealPy} retire %i";
      ProtectSystem = "strict";
      ReadWritePaths = [ "${spool}" ];
      PrivateNetwork = true;
      NoNewPrivileges = true;
    };
  };

  # Let axis start/stop ONLY instances of this template. Scope must be confirmed in the VM:
  # the rule text alone does not establish what systemd passes to polkit.
  # Workdir repair (reviewable): enable polkit; cover manage-units/manage-unit-files;
  # unit match with optional .service suffix (systemd/polkit variance across versions).
  security.polkit.enable = true;
  security.polkit.extraConfig = ''
    polkit.addRule(function(action, subject) {
      if (subject.user != "axis")
        return;
      if (action.id != "org.freedesktop.systemd1.manage-units" &&
          action.id != "org.freedesktop.systemd1.manage-unit-files")
        return;
      var unit = action.lookup("unit") || action.lookup("name") || "";
      var verb = action.lookup("verb") || "";
      if (/^axis-acceptance(-seal)?@[a-z0-9-]+(\.service)?$/.test(unit) &&
          (verb == "start" || verb == "stop" || verb == "")) {
        return polkit.Result.YES;
      }
    });
  '';
}
