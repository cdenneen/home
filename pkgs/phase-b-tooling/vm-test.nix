{
  lib,
  pkgs,
  module,
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
  schemaNames = [
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
  ];
  digest = character: "sha256:${lib.concatStrings (lib.replicate 64 character)}";
  signers = builtins.listToAttrs (
    lib.imap0 (index: role: {
      name = role;
      value = {
        id = "vm-${role}";
        algorithm = "ed25519-vm";
        public_key = "vm-key-${toString index}";
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
  schemaDigests = builtins.listToAttrs (
    map (name: {
      inherit name;
      value = digest "b";
    }) schemaNames
  );
in
(pkgs.testers.runNixOSTest {
  name = "phase-b-tooling-vm";
  nodes.machine = { ... }: {
    imports = [ module ];
    users.users.phaseb-source = {
      isNormalUser = true;
      uid = 1234;
      group = "phaseb-source";
    };
    users.groups.phaseb-source.gid = 1234;
    systemd.tmpfiles.rules = [ "f /var/lib/systemd/linger/phaseb-source 0644 root root -" ];
    systemd.user.services.phaseb-vm-target = {
      wantedBy = [ "default.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${pkgs.coreutils}/bin/true";
      };
    };
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
          machine_id = "vm-machine";
          host_identity = "vm-host";
          boot_id = "vm-boot";
          user_manager_id = "vm-manager";
          home_generation = "vm-home";
          booted_closure = digest "4";
          user_manager_machine = ".host";
        };
        registryPaths = map (index: "/var/empty/registry-${toString index}/cron/jobs.json") [
          1
          2
          3
          4
          5
          6
        ];
        collectorIdentity = "vm-offhost-receiver";
        authorityIdentities = {
          generic = authority "generic";
          alpha0 = authority "alpha0";
        };
        effectPlanDigest = digest "5";
        rollbackPlanDigest = digest "6";
        canonicalVectorsDigest = digest "7";
        processInventoryDigest = digest "8";
        listenerInventoryDigest = digest "9";
        runbookDigest = digest "c";
        inherit schemaDigests;
      };
    };
  };
  testScript = ''
    machine.start()
    machine.wait_for_unit("multi-user.target")
    machine.succeed("test $(stat -c %U:%G:%a /etc/phase-b) = root:root:700")
    machine.succeed("test $(stat -c %U:%G:%a /etc/phase-b/trust.json) = root:root:400")
    machine.succeed("! systemctl is-active 'phase-b-executor@vm.service'")
    machine.succeed("! systemctl is-active 'phase-b-verifier@vm.service'")
    machine.succeed("! systemctl is-active phase-b-source-sensor.service")
    machine.succeed("! systemctl is-active phase-b-source-sensor.socket")
    machine.succeed("! systemctl is-active phase-b-custody-reader.service")
    machine.succeed("! systemctl is-active phase-b-custody-reader.socket")
    machine.succeed("systemctl cat 'phase-b-executor@.service' | grep -F '${phaseB}/bin/phase-b-execute'")
    machine.succeed("systemctl cat 'phase-b-executor@.service' | grep -F 'Requires=phase-b-source-sensor.socket phase-b-custody-reader.socket'")
    machine.succeed("systemctl cat phase-b-source-sensor.service | grep -F 'StandardInput=socket'")
    machine.succeed("systemctl cat phase-b-source-sensor.service | grep -F 'RestrictAddressFamilies=AF_UNIX'")
    machine.succeed("test \"$(systemctl show -p StopWhenUnneeded --value phase-b-source-sensor.socket)\" = yes")
    machine.succeed("systemctl show -p PartOf --value phase-b-source-sensor.service | grep -Fw phase-b-source-sensor.socket")
    machine.succeed("test \"$(systemctl show -p StopWhenUnneeded --value phase-b-custody-reader.socket)\" = yes")
    machine.succeed("systemctl show -p PartOf --value phase-b-custody-reader.service | grep -Fw phase-b-custody-reader.socket")
    machine.succeed("systemctl show -p PropagatesStopTo --value 'phase-b-executor@vm.service' | grep -Fw phase-b-source-sensor.socket")
    machine.succeed("systemctl show -p PropagatesStopTo --value 'phase-b-executor@vm.service' | grep -Fw phase-b-custody-reader.socket")
    machine.succeed("systemctl cat phase-b-custody-reader.service | grep -F 'DynamicUser=true'")
    machine.succeed("systemctl cat phase-b-custody-reader.service | grep -F 'User=phase-b-custody-reader'")
    machine.succeed("systemctl cat phase-b-custody-reader.service | grep -Fx 'InaccessiblePaths=/var/lib/phase-b'")
    machine.succeed("systemctl cat phase-b-custody-reader.service | grep -Fx 'InaccessiblePaths=/etc/phase-b'")
    machine.succeed("systemctl cat phase-b-custody-reader.service | grep -Fx 'InaccessiblePaths=/home'")
    machine.succeed("systemctl cat phase-b-custody-reader.service | grep -Fx 'InaccessiblePaths=/root'")
    machine.succeed("! systemctl cat phase-b-custody-reader.service | grep -F 'ReadWritePaths='")
    machine.succeed("systemctl cat phase-b-custody-reader.service | grep -F 'IPAddressDeny=any'")
    machine.succeed("systemctl cat phase-b-custody-reader.service | grep -F 'IPAddressAllow=198.51.100.20/32'")
    machine.succeed("systemctl cat 'phase-b-executor@.service' | grep -F 'ProtectHome=tmpfs'")
    machine.wait_for_unit("user@1234.service")
    machine.succeed("systemctl --user --machine=phaseb-source@.host is-active phaseb-vm-target.service")
    machine.succeed("systemctl --user --machine=phaseb-source@.host stop phaseb-vm-target.service")
    machine.succeed("! systemctl --user --machine=phaseb-source@.host is-active phaseb-vm-target.service")
    machine.succeed("PYTHONPATH=${phaseB}/lib/phase-b:${phaseB}/lib/phase-b/tests ${pkgs.python3}/bin/python -m unittest -v test_registry_journal.RegistryTests.test_exact_atomic_replace_pause_sequence_is_accepted test_registry_journal.JournalTests.test_anonymous_publication_fault_boundaries_are_atomic test_cli_receiver.ReceiverTests.test_durable_cas_is_one_time_and_bool_is_not_counter test_strict_json_trust.TrustTests.test_production_cli_rejects_options_and_missing_anchor", timeout=300)
  '';
}).overrideTestDerivation
  (_: {
    # GitHub's aarch64 runner exposes nixos-test but not /dev/kvm; QEMU's
    # deterministic TCG fallback is sufficient for this dormant module smoke test.
    requiredSystemFeatures = [ "nixos-test" ];
  })
