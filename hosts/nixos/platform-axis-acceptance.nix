# Proposed addition to github.com/cdenneen/home: hosts/nixos/ghost.nix (imported as a module).
# Base: tip parent of eng/axis-r8-platform-prep. DRAFT - not merged, not activated.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  spool = "/var/lib/axis-acceptance";
  python = pkgs.python312; # interpreter is platform-owned, not chosen by AXIS or the candidate
  runnerVersion = "axis.acceptance-runner.v1";

  # Fixed, scenario-agnostic entry point. It never interprets job data as shell: the job's
  # module name and argument vector are read as JSON by a platform-owned Python stub, validated
  # against a strict pattern, and passed to execv as a list. No scenario name is baked in here,
  # so new scenarios need no host change.
  # Before exec: re-digest published snapshot and require equality with SEALED (bind published
  # bytes to the recorded candidate identity; closes the seal-compute-then-copy interval).
  entryPy = pkgs.writeText "axis-acceptance-entry.py" ''
    import hashlib, json, os, pathlib, re, sys
    VERSION = "axis.tree-manifest.v1"
    job_id = sys.argv[1]
    if not re.fullmatch(r"[a-z0-9-]{8,64}", job_id):
        sys.exit(64)
    job = pathlib.Path("${spool}/jobs") / job_id
    sealed_path = job / "SEALED"
    if not sealed_path.is_file():
        sys.exit(65)
    # Partial names (.sealing / .publishing) are not this path; atomic rename publishes
    # only a complete tree at jobs/<id>. Digest bind below rejects divergent bytes.
    def tree_digest(root):
        h = hashlib.sha256((VERSION + "\n").encode())
        for p in sorted(root.rglob("*"), key=lambda q: q.relative_to(root).as_posix()):
            rel = p.relative_to(root).as_posix()
            st = p.lstat()
            if p.is_symlink():
                e = {"path": rel, "type": "symlink", "target": os.readlink(p)}
            elif p.is_dir():
                e = {"path": rel, "type": "dir", "mode": st.st_mode & 0o7777}
            elif p.is_file():
                e = {"path": rel, "type": "file", "mode": st.st_mode & 0o7777, "size": st.st_size,
                     "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            else:
                sys.exit(65)
            blob = json.dumps(e, sort_keys=True, separators=(",", ":")).encode()
            h.update(len(blob).to_bytes(8, "big") + blob)
        return h.hexdigest()
    want = sealed_path.read_text().strip()
    got = tree_digest(job / "snapshot")
    if got != want:
        sys.exit(65)
    with open(job / "case.json") as fh:
        case = json.load(fh)
    module = case["module"]
    argv = case["argv"]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", module):
        sys.exit(66)
    if not (isinstance(argv, list) and len(argv) <= 32
            and all(isinstance(a, str) and len(a) <= 256 and "\\0" not in a for a in argv)):
        sys.exit(67)
    os.chdir(job / "snapshot" / "src")
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
  contract = pkgs.writeText "axis-acceptance-runner.json" (
    builtins.toJSON {
      interface = runnerVersion;
      spool = spool;
      unit_template = "axis-acceptance@.service";
      entry = "${entry}";
      interpreter = "${python}/bin/python3";
      max_runtime_s = 30;
      max_tasks = 32;
      max_memory = "256M";
    }
  );
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

      StandardOutput = "file:${spool}/jobs/%i/result/stdout"; # see LIFECYCLE: result/ is the one writable path
      StandardError = "file:${spool}/jobs/%i/result/stderr";
    };
  };

  # Root-owned seal step. Moves a staged job out of axis's reach, makes it root-owned and
  # read-only, recomputes the tree digest itself, writes SEALED, publishes via private
  # staging + atomic rename, and binds published snapshot bytes to that digest.
  systemd.services."axis-acceptance-seal@" = {
    description = "Seal AXIS acceptance job %i";
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${python}/bin/python3 -E -s -B ${sealPy} seal %i";
      ExecStop = "${python}/bin/python3 -E -s -B ${sealPy} retire %i";
      ProtectSystem = "strict";
      ReadWritePaths = [ "${spool}" ];
      PrivateNetwork = true;
      NoNewPrivileges = true;
    };
  };

  # Polkit contract: start/stop ONLY on the two acceptance templates.
  #
  # Installed systemd authorization behavior (systemd 260 on this platform; see
  # org.freedesktop.systemd1.policy and bus_verify_manage_units_async_full):
  # - systemctl start|stop <unit> -> action org.freedesktop.systemd1.manage-units
  #   with details unit=<primary unit id including .service> and verb=start|stop.
  #   Verb and unit are always supplied for these unit operations.
  # - enable/disable/link/edit and related unit-file ops -> manage-unit-files, which
  #   policy-annotates imply of reload-daemon + manage-units. That is broader than
  #   the approved start/stop-only contract and is NOT granted.
  # - Empty/missing unit or verb is rejected (Result.NO), not treated as allow.
  security.polkit.enable = true;
  security.polkit.extraConfig = ''
    polkit.addRule(function(action, subject) {
      if (subject.user != "axis")
        return;
      if (action.id == "org.freedesktop.systemd1.manage-unit-files")
        return polkit.Result.NO;
      if (action.id != "org.freedesktop.systemd1.manage-units")
        return;
      var unit = action.lookup("unit");
      var verb = action.lookup("verb");
      if (!unit || !verb)
        return polkit.Result.NO;
      if (/^axis-acceptance(-seal)?@[a-z0-9-]+\.service$/.test(unit) &&
          (verb == "start" || verb == "stop")) {
        return polkit.Result.YES;
      }
      return polkit.Result.NO;
    });
  '';
}
