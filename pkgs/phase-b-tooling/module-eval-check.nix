{
  lib,
  pkgs,
  module,
  nixosSystem,
  phaseB,
}:
let
  roles = [
    "source-authorization"
    "source-execution"
    "offhost-collection"
    "reconstruction"
    "receipt"
    "sensor-audit"
    "sensor-user-journal"
    "sensor-systemd"
    "sensor-registry"
    "sensor-database"
    "sensor-provider-route"
    "sensor-custody"
    "sensor-identity"
    "sensor-time"
  ];
  externalNames = [
    "signature-verifier"
    "systemctl"
    "hermes"
    "reconstruction-runner"
    "network-monitor"
    "write-monitor"
    "process-inspector"
    "custody-reader"
    "source-sensor"
    "artifact-reader"
    "hermes-mutation-adapter"
    "privilege-dropper"
    "receiver-client"
    "observation-signer"
    "source-signer"
  ];
  digest = character: "sha256:${lib.concatStrings (lib.replicate 64 character)}";
  signers = builtins.listToAttrs (
    lib.imap0 (index: role: {
      name = role;
      value = {
        id = "fixture-${role}";
        algorithm = "ed25519-fixture";
        public_key = "fixture-key-${toString index}";
      };
    }) roles
  );
  externalExecutables = builtins.listToAttrs (
    map (name: {
      inherit name;
      value = {
        path = "${pkgs.coreutils}/bin/true";
        closure = toString pkgs.coreutils;
        digest = digest "a";
      };
    }) externalNames
  );
  authority = prefix: {
    route_identity = "${prefix}-route";
    service_identity = "${prefix}-service";
    session_identity = "${prefix}-session";
    profile_identity = "${prefix}-profile";
  };
  disabled = nixosSystem {
    system = pkgs.stdenv.hostPlatform.system;
    modules = [
      module
      { system.stateVersion = "25.11"; }
    ];
  };
  offhost = nixosSystem {
    system = pkgs.stdenv.hostPlatform.system;
    modules = [
      module
      {
        system.stateVersion = "25.11";
        services.phaseBOffhostCollector = {
          enable = true;
          trustAnchorFile = pkgs.writeText "reviewed-phase-b-trust.json" "{}";
        };
      }
    ];
  };
  enabled = nixosSystem {
    system = pkgs.stdenv.hostPlatform.system;
    modules = [
      module
      {
        system.stateVersion = "25.11";
        fileSystems."/" = {
          device = "/dev/vda";
          fsType = "ext4";
        };
        boot.loader.grub.devices = [ "/dev/vda" ];
        users.users.phaseb-source = {
          isNormalUser = true;
          uid = 1234;
          group = "phaseb-source";
        };
        users.groups.phaseb-source.gid = 1234;
        services.phaseBQualification = {
          enable = true;
          receiverIPAddressAllow = [ "192.0.2.10/32" ];
          custodyReaderIPAddressAllow = [ "198.51.100.20/32" ];
          trust = {
            anchorGeneration = 1;
            inherit signers externalExecutables;
            packageExecutableDigests = {
              executor = digest "1";
              collector = digest "2";
              verifier = digest "3";
            };
            source = {
              uid = 1234;
              gid = 1234;
              user = "phaseb-source";
              home = "/var/empty/phaseb-source";
              machine_id = "fixture-machine";
              host_identity = "fixture-host";
              boot_id = "fixture-boot";
              user_manager_id = "fixture-manager";
              home_generation = "fixture-home-generation";
              booted_closure = digest "4";
              user_manager_machine = ".host";
            };
            registryPaths = map (index: "/var/empty/registry-${toString index}/jobs.json") [
              1
              2
              3
              4
              5
              6
            ];
            collectorIdentity = "fixture-offhost-collector";
            authorityIdentities = {
              generic = authority "generic";
              alpha0 = authority "alpha0";
            };
            effectPlanDigest = digest "5";
            rollbackPlanDigest = digest "6";
            canonicalVectorsDigest = digest "7";
            processInventoryDigest = digest "8";
            listenerInventoryDigest = digest "9";
            runbookDigest = digest "b";
            schemaDigests = builtins.listToAttrs (
              lib.imap0
                (index: name: {
                  inherit name;
                  value = digest (
                    builtins.elemAt [
                      "0"
                      "1"
                      "2"
                      "3"
                      "4"
                      "5"
                      "6"
                      "7"
                      "8"
                      "9"
                      "a"
                      "b"
                      "c"
                      "d"
                    ] index
                  );
                })
                [
                  "baseline"
                  "consumption"
                  "f0"
                  "journal"
                  "observation"
                  "operation-grant"
                  "incident-rollback-grant"
                  "raw-batch"
                  "receipt"
                  "reconstruction"
                  "receiver-refresh-segment"
                  "signed-envelope"
                  "source-event"
                  "trust"
                ]
            );
          };
        };
      }
    ];
  };
  invalidPath =
    path:
    enabled.extendModules {
      modules = [
        {
          services.phaseBQualification.trust.externalExecutables.signature-verifier.path = lib.mkForce path;
        }
      ];
    };
  invalidPaths = map invalidPath [
    "${pkgs.coreutils}/bin/true/."
    "${pkgs.coreutils}/bin/../true"
    "${pkgs.coreutils}//bin/true"
  ];
  invalidSourcePaths =
    map
      (
        path:
        enabled.extendModules {
          modules = [
            { services.phaseBQualification.trust.source.home = lib.mkForce path; }
          ];
        }
      )
      [
        "/var/empty/../phaseb-source"
        "/var//empty/phaseb-source"
        "/var/empty/."
      ];
  disabledServices = disabled.config.systemd.services;
  enabledServices = enabled.config.systemd.services;
  offhostServices = offhost.config.systemd.services;
  activation = enabled.config.system.activationScripts.phaseBTrustLifecycle.text;
  anchorFile = enabled.config.system.build.phaseBTrustAnchor;
  executor = enabledServices."phase-b-executor@";
  verifier = enabledServices."phase-b-verifier@";
  sourceSensor = enabledServices.phase-b-source-sensor;
  custodyReader = enabledServices.phase-b-custody-reader;
  sourceSocket = enabled.config.systemd.sockets.phase-b-source-sensor;
  custodySocket = enabled.config.systemd.sockets.phase-b-custody-reader;
in
assert lib.all (item: item.assertion) enabled.config.assertions;
assert lib.all (system: !(lib.all (item: item.assertion) system.config.assertions)) invalidPaths;
assert lib.all (
  system: !(lib.all (item: item.assertion) system.config.assertions)
) invalidSourcePaths;
assert !(disabled.config.system.activationScripts ? phaseBTrustLifecycle);
assert !(builtins.hasAttr "phase-b-executor@" disabledServices);
assert !(builtins.hasAttr "phase-b-verifier@" disabledServices);
assert builtins.hasAttr "phase-b-executor@" enabledServices;
assert builtins.hasAttr "phase-b-verifier@" enabledServices;
assert builtins.hasAttr "phase-b-source-sensor" enabledServices;
assert builtins.hasAttr "phase-b-custody-reader" enabledServices;
assert builtins.hasAttr "phase-b-source-sensor" enabled.config.systemd.sockets;
assert builtins.hasAttr "phase-b-custody-reader" enabled.config.systemd.sockets;
assert !(builtins.hasAttr "phase-b-offhost-collector@" enabledServices);
assert builtins.hasAttr "phase-b-offhost-collector@" offhostServices;
assert !(builtins.hasAttr "phase-b-executor@" offhostServices);
assert !(builtins.hasAttr "phase-b-source-sensor" offhostServices);
assert (offhostServices."phase-b-offhost-collector@".wantedBy or [ ]) == [ ];
assert (executor.wantedBy or [ ]) == [ ];
assert (verifier.wantedBy or [ ]) == [ ];
assert (sourceSensor.wantedBy or [ ]) == [ ];
assert (custodyReader.wantedBy or [ ]) == [ ];
assert (sourceSocket.wantedBy or [ ]) == [ ];
assert (custodySocket.wantedBy or [ ]) == [ ];
assert executor.serviceConfig.ExecStart == "${phaseB}/bin/phase-b-execute";
assert verifier.serviceConfig.ExecStart == "${phaseB}/bin/phase-b-verify";
assert sourceSensor.serviceConfig.User == "phaseb-source";
assert sourceSensor.serviceConfig.StandardInput == "socket";
assert sourceSensor.serviceConfig.RestrictAddressFamilies == [ "AF_UNIX" ];
assert !(sourceSensor.unitConfig.StopWhenUnneeded or false);
assert sourceSensor.partOf == [ "phase-b-source-sensor.socket" ];
assert sourceSocket.unitConfig.StopWhenUnneeded;
assert custodyReader.serviceConfig.StandardInput == "socket";
assert custodyReader.serviceConfig.DynamicUser;
assert custodyReader.serviceConfig.User == "phase-b-custody-reader";
assert custodyReader.serviceConfig.Group == "phase-b-custody-reader";
assert custodyReader.serviceConfig.InaccessiblePaths == [
  "/var/lib/phase-b"
  "/etc/phase-b"
  "/home"
  "/root"
];
assert custodyReader.serviceConfig.IPAddressAllow == [ "198.51.100.20/32" ];
assert custodyReader.serviceConfig.IPAddressDeny == "any";
assert (custodyReader.serviceConfig.ReadWritePaths or [ ]) == [ ];
assert !(custodyReader.unitConfig.StopWhenUnneeded or false);
assert custodyReader.partOf == [ "phase-b-custody-reader.socket" ];
assert custodySocket.unitConfig.StopWhenUnneeded;
assert builtins.elem "phase-b-source-sensor.socket" executor.requires;
assert builtins.elem "phase-b-custody-reader.socket" executor.requires;
assert executor.unitConfig.PropagatesStopTo == [
  "phase-b-source-sensor.socket"
  "phase-b-custody-reader.socket"
];
assert executor.serviceConfig.DynamicUser == false;
assert verifier.serviceConfig.DynamicUser == false;
assert executor.serviceConfig.IPAddressDeny == "any";
assert verifier.serviceConfig.IPAddressDeny == "any";
assert lib.hasInfix "/etc/phase-b/trust.json" activation;
assert lib.hasInfix "-m 0400" activation;
assert lib.hasInfix "-m 0700 /etc/phase-b" activation;
assert builtins.elem "d /var/lib/phase-b 0700 root root -" enabled.config.systemd.tmpfiles.rules;
pkgs.runCommand "phase-b-module-eval-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
  python - ${anchorFile} <<'PY'
  import json
  import sys

  with open(sys.argv[1], encoding="utf-8") as source:
      anchor = json.load(source)
  assert anchor["namespace_roles"]["phase-b-operation-grant"] == "source-authorization"
  assert anchor["namespace_roles"]["phase-b-incident-rollback-grant"] == "source-authorization"
  assert "incident-rollback-grant" in anchor["schema_digests"]
  PY
  printf '%s\n' \
    'disabled-by-default' \
    'fixed-store-execstart' \
    'root-anchor-0400' \
    'owner-only-state' \
    'no-source-host-collector' > "$out"
''
