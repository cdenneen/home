{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib)
    all
    attrNames
    hasPrefix
    length
    mkEnableOption
    mkIf
    mkMerge
    mkOption
    types
    unique
    ;
  cfg = config.services.phaseBQualification;
  collectorCfg = config.services.phaseBOffhostCollector;
  phaseB = pkgs.callPackage ../../../pkgs/phase-b-tooling.nix { };
  json = pkgs.formats.json { };

  digestType = types.strMatching "sha256:[0-9a-f]{64}";
  absolutePathType = types.strMatching "/.*";
  storeObjectType = types.strMatching "/nix/store/[0-9a-z]{32}-[^/]+";
  storeExecutableType = types.strMatching "/nix/store/[0-9a-z]{32}-[^/]+/[^[:space:]]+";
  identifierType = types.strMatching "[^[:space:]]+";
  normalizedAbsolute =
    value:
    let
      parts = lib.splitString "/" value;
    in
    value != "/"
    && builtins.head parts == ""
    && all (part: part != "" && part != "." && part != "..") (builtins.tail parts);

  requiredRoles = [
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
  namespaceRoles = {
    phase-b-baseline = "source-authorization";
    phase-b-operation-grant = "source-authorization";
    phase-b-incident-rollback-grant = "source-authorization";
    phase-b-f0 = "source-execution";
    phase-b-observation = "offhost-collection";
    phase-b-reconstruction = "reconstruction";
    phase-b-receipt = "receipt";
    phase-b-backup-restore = "source-authorization";
    phase-b-consumption-grant = "source-authorization";
    "phase-b-source-event.audit" = "sensor-audit";
    "phase-b-source-event.user-journal" = "sensor-user-journal";
    "phase-b-source-event.systemd" = "sensor-systemd";
    "phase-b-source-event.registry" = "sensor-registry";
    "phase-b-source-event.database" = "sensor-database";
    "phase-b-source-event.provider-route" = "sensor-provider-route";
    "phase-b-source-event.custody" = "sensor-custody";
    "phase-b-source-event.identity" = "sensor-identity";
    "phase-b-source-event.time" = "sensor-time";
  };
  requiredExternalExecutables = [
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
  requiredSchemas = [
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

  signerType = types.submodule {
    options = {
      id = mkOption { type = identifierType; };
      algorithm = mkOption { type = identifierType; };
      public_key = mkOption { type = identifierType; };
    };
  };
  executableType = types.submodule {
    options = {
      path = mkOption { type = storeExecutableType; };
      closure = mkOption { type = storeObjectType; };
      digest = mkOption { type = digestType; };
    };
  };
  authorityType = types.submodule {
    options = {
      route_identity = mkOption { type = identifierType; };
      service_identity = mkOption { type = identifierType; };
      session_identity = mkOption { type = identifierType; };
      profile_identity = mkOption { type = identifierType; };
    };
  };

  packageExecutables = {
    executor = {
      path = "${phaseB}/bin/phase-b-execute";
      closure = toString phaseB;
      digest = cfg.trust.packageExecutableDigests.executor;
    };
    collector = {
      path = "${phaseB}/bin/phase-b-collect";
      closure = toString phaseB;
      digest = cfg.trust.packageExecutableDigests.collector;
    };
    verifier = {
      path = "${phaseB}/bin/phase-b-verify";
      closure = toString phaseB;
      digest = cfg.trust.packageExecutableDigests.verifier;
    };
  };
  anchor = {
    schema = "phase-b.trust.v2";
    anchor_generation = cfg.trust.anchorGeneration;
    signers = cfg.trust.signers;
    namespace_roles = namespaceRoles;
    executables = packageExecutables // cfg.trust.externalExecutables;
    source = cfg.trust.source;
    registry_paths = cfg.trust.registryPaths;
    collector_identity = cfg.trust.collectorIdentity;
    authority_identities = {
      generic = cfg.trust.authorityIdentities.generic;
      alpha0 = cfg.trust.authorityIdentities.alpha0;
      dedicated_axis_route = "ABSENT";
    };
    effect_plan_digest = cfg.trust.effectPlanDigest;
    rollback_plan_digest = cfg.trust.rollbackPlanDigest;
    canonical_vectors_digest = cfg.trust.canonicalVectorsDigest;
    process_inventory_digest = cfg.trust.processInventoryDigest;
    listener_inventory_digest = cfg.trust.listenerInventoryDigest;
    runbook_digest = cfg.trust.runbookDigest;
    schema_digests = cfg.trust.schemaDigests;
  };
  anchorFile = json.generate "phase-b-trust-v2.json" anchor;
  selectedAnchorFile = if cfg.enable then anchorFile else collectorCfg.trustAnchorFile;

  registryHomes = unique (map (path: builtins.dirOf (builtins.dirOf path)) cfg.trust.registryPaths);

  commonHardening = {
    Type = "oneshot";
    DynamicUser = false;
    NoNewPrivileges = true;
    PrivateTmp = true;
    PrivateDevices = true;
    ProtectClock = true;
    ProtectControlGroups = true;
    ProtectHostname = true;
    ProtectKernelLogs = true;
    ProtectKernelModules = true;
    ProtectKernelTunables = true;
    ProtectSystem = "strict";
    ProtectHome = "tmpfs";
    RestrictAddressFamilies = [ "AF_UNIX" ];
    RestrictNamespaces = true;
    LockPersonality = true;
    MemoryDenyWriteExecute = true;
    RemoveIPC = true;
    SystemCallArchitectures = "native";
    UMask = "0077";
    IPAddressDeny = "any";
  };
in
{
  options.services.phaseBOffhostCollector = {
    enable = mkEnableOption "dormant independently runnable off-host Phase B collector";
    trustAnchorFile = mkOption {
      type = types.path;
      default = /dev/null;
      description = "Separately reviewed canonical trust.json source, fixed by Nix source rather than signed input.";
    };
  };

  options.services.phaseBQualification = {
    enable = mkEnableOption "dormant, manually started Phase B fencing qualification machinery";
    receiverIPAddressAllow = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "Root-reviewed receiver IP/CIDR allowlist for online one-time receipt consumption.";
    };
    custodyReaderIPAddressAllow = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "Root-reviewed fixed provider IP/CIDR allowlist used only by the isolated custody reader service.";
    };

    trust = {
      anchorGeneration = mkOption {
        type = types.ints.positive;
        description = "Monotonic root trust-anchor generation.";
      };
      signers = mkOption {
        type = types.attrsOf signerType;
        description = "Exact independent signer-role bindings.";
      };
      packageExecutableDigests = {
        executor = mkOption { type = digestType; };
        collector = mkOption { type = digestType; };
        verifier = mkOption { type = digestType; };
      };
      externalExecutables = mkOption {
        type = types.attrsOf executableType;
        description = "Exact immutable external command closures.";
      };
      source = {
        uid = mkOption { type = types.ints.unsigned; };
        gid = mkOption { type = types.ints.unsigned; };
        user = mkOption { type = identifierType; };
        home = mkOption { type = absolutePathType; };
        machine_id = mkOption { type = identifierType; };
        host_identity = mkOption { type = identifierType; };
        boot_id = mkOption { type = identifierType; };
        user_manager_id = mkOption { type = identifierType; };
        home_generation = mkOption { type = identifierType; };
        booted_closure = mkOption { type = digestType; };
        user_manager_machine = mkOption { type = identifierType; };
      };
      registryPaths = mkOption {
        type = types.listOf absolutePathType;
        description = "The exact six physical jobs.json paths.";
      };
      collectorIdentity = mkOption { type = identifierType; };
      authorityIdentities = {
        generic = mkOption { type = authorityType; };
        alpha0 = mkOption { type = authorityType; };
      };
      effectPlanDigest = mkOption { type = digestType; };
      rollbackPlanDigest = mkOption { type = digestType; };
      canonicalVectorsDigest = mkOption { type = digestType; };
      processInventoryDigest = mkOption { type = digestType; };
      listenerInventoryDigest = mkOption { type = digestType; };
      runbookDigest = mkOption { type = digestType; };
      schemaDigests = mkOption { type = types.attrsOf digestType; };
    };
  };

  config = mkMerge [
    (mkIf (cfg.enable || collectorCfg.enable) {
      system.build.phaseBTrustAnchor = selectedAnchorFile;
      system.activationScripts.phaseBTrustLifecycle = {
        deps = [ "etc" ];
        text = ''
          install=${pkgs.coreutils}/bin/install
          mv=${pkgs.coreutils}/bin/mv
          "$install" -d -o 0 -g 0 -m 0700 /etc/phase-b
          "$install" -o 0 -g 0 -m 0400 ${selectedAnchorFile} /etc/phase-b/.trust.json.new
          "$mv" -fT /etc/phase-b/.trust.json.new /etc/phase-b/trust.json
        '';
      };
    })
    {
      assertions = [
        {
          assertion = !(cfg.enable && collectorCfg.enable);
          message = "Phase B source executor and off-host collector roles must not share a host declaration";
        }
        {
          assertion = !collectorCfg.enable || hasPrefix "/nix/store/" (toString collectorCfg.trustAnchorFile);
          message = "Off-host Phase B trust anchor must be a separately reviewed Nix store source";
        }
      ];
    }
    (mkIf collectorCfg.enable {
      environment.systemPackages = [ phaseB ];
      systemd.tmpfiles.rules = [
        "d /var/lib/phase-b-collector 0700 root root -"
        "d /var/lib/phase-b-collector/inbox 0700 root root -"
        "d /var/lib/phase-b-collector/inbox/events 0700 root root -"
        "d /var/lib/phase-b-collector/artifacts 0700 root root -"
        "d /var/lib/phase-b-collector/artifacts/evidence 0700 root root -"
        "d /var/lib/phase-b-collector/state 0700 root root -"
      ];
      systemd.services."phase-b-offhost-collector@" = {
        description = "Dormant independently runnable off-host Phase B collector (%i)";
        serviceConfig = commonHardening // {
          User = "root";
          Group = "root";
          ExecStart = "${phaseB}/bin/phase-b-collect";
          ReadWritePaths = [ "/var/lib/phase-b-collector" ];
          CapabilityBoundingSet = "";
        };
      };
    })
    (mkIf cfg.enable {
      assertions = [
        {
          assertion = attrNames cfg.trust.signers == lib.sort builtins.lessThan requiredRoles;
          message = "Phase B trust must define exactly the fixed independent signer roles";
        }
        {
          assertion =
            let
              values = builtins.attrValues cfg.trust.signers;
            in
            length (unique (map (item: item.id) values)) == length requiredRoles
            && length (unique (map (item: item.public_key) values)) == length requiredRoles
            && length (unique (map (item: item.algorithm) values)) == 1
            && all (item: item.algorithm != "hmac-sha256-test") values;
          message = "Phase B signer IDs and keys must be role-unique and use one anchored algorithm";
        }
        {
          assertion =
            attrNames cfg.trust.externalExecutables == lib.sort builtins.lessThan requiredExternalExecutables;
          message = "Phase B trust must bind exactly the fixed external executable set";
        }
        {
          assertion = all (
            item:
            hasPrefix "/nix/store/" item.closure
            && hasPrefix "${item.closure}/" item.path
            && normalizedAbsolute item.closure
            && normalizedAbsolute item.path
            && item.path != item.closure
          ) (builtins.attrValues cfg.trust.externalExecutables);
          message = "Phase B external executables must be exact paths inside bound Nix store closures";
        }
        {
          assertion =
            normalizedAbsolute cfg.trust.source.home
            && all normalizedAbsolute cfg.trust.registryPaths
            && length cfg.trust.registryPaths == 6
            && length (unique cfg.trust.registryPaths) == 6;
          message = "Phase B trust must bind a normalized source home and exactly six normalized distinct registry paths";
        }
        {
          assertion = cfg.receiverIPAddressAllow != [ ];
          message = "Phase B verifier requires a non-empty root-reviewed off-host receiver IP allowlist";
        }
        {
          assertion = cfg.custodyReaderIPAddressAllow != [ ];
          message = "Phase B custody reader requires a non-empty root-reviewed provider IP allowlist";
        }
        {
          assertion = attrNames cfg.trust.schemaDigests == lib.sort builtins.lessThan requiredSchemas;
          message = "Phase B trust must bind exactly the shipped evidence schemas";
        }
        {
          assertion =
            cfg.trust.source.user != "root"
            && builtins.hasAttr cfg.trust.source.user config.users.users
            && (builtins.getAttr cfg.trust.source.user config.users.users).uid == cfg.trust.source.uid
            && builtins.hasAttr cfg.trust.source.user config.users.groups
            && (builtins.getAttr cfg.trust.source.user config.users.groups).gid == cfg.trust.source.gid;
          message = "Phase B source identity must be an existing non-root user with the anchored UID";
        }
        {
          assertion =
            length (
              unique (
                builtins.attrValues cfg.trust.authorityIdentities.generic
                ++ builtins.attrValues cfg.trust.authorityIdentities.alpha0
              )
            ) == 8;
          message = "Phase B generic and Alpha0 authority identities must all be distinct";
        }
      ];

      environment.systemPackages = [ phaseB ];

      systemd.tmpfiles.rules = [
        "d /var/lib/phase-b 0700 root root -"
        "d /var/lib/phase-b/inputs 0700 root root -"
        "d /var/lib/phase-b/artifacts 0700 root root -"
        "d /var/lib/phase-b/artifacts/evidence 0700 root root -"
        "d /var/lib/phase-b/journals 0700 root root -"
        "d /var/lib/phase-b/receipts 0700 root root -"
      ];

      # No WantedBy/RequiredBy: these templates can only be started by an
      # separately authorized operator. Merging or switching cannot execute B0-B4.
      systemd.services."phase-b-executor@" = {
        description = "Dormant root Phase B executor (%i)";
        unitConfig.PropagatesStopTo = [
          "phase-b-source-sensor.socket"
          "phase-b-custody-reader.socket"
        ];
        requires = [
          "phase-b-source-sensor.socket"
          "phase-b-custody-reader.socket"
        ];
        after = [
          "phase-b-source-sensor.socket"
          "phase-b-custody-reader.socket"
        ];
        serviceConfig = commonHardening // {
          User = "root";
          Group = "root";
          ExecStart = "${phaseB}/bin/phase-b-execute";
          ReadWritePaths = [ "/var/lib/phase-b" ] ++ registryHomes;
          BindPaths = registryHomes;
          CapabilityBoundingSet = [
            "CAP_DAC_OVERRIDE"
            "CAP_KILL"
            "CAP_SETGID"
            "CAP_SETUID"
          ];
        };
      };

      systemd.services."phase-b-verifier@" = {
        description = "Dormant root Phase B receipt verifier (%i)";
        serviceConfig = commonHardening // {
          User = "root";
          Group = "root";
          ExecStart = "${phaseB}/bin/phase-b-verify";
          ReadOnlyPaths = [ "/var/lib/phase-b" ];
          RestrictAddressFamilies = [
            "AF_UNIX"
            "AF_INET"
            "AF_INET6"
          ];
          IPAddressAllow = cfg.receiverIPAddressAllow;
          CapabilityBoundingSet = "";
        };
      };

      # The executor only runs fixed clients. Private signing material and live
      # capture authority remain in these separately sandboxed, socket-activated
      # services. The sockets have no WantedBy/RequiredBy and therefore remain
      # dormant until an explicitly started executor requires them.
      systemd.sockets.phase-b-source-sensor = {
        description = "Dormant Phase B source sensor socket";
        unitConfig.StopWhenUnneeded = true;
        socketConfig = {
          ListenStream = "/run/phase-b/source-sensor.sock";
          SocketMode = "0600";
          RemoveOnStop = true;
        };
      };
      systemd.services.phase-b-source-sensor = {
        description = "Isolated Phase B source sensor";
        partOf = [ "phase-b-source-sensor.socket" ];
        serviceConfig = commonHardening // {
          Type = "simple";
          User = cfg.trust.source.user;
          Group = toString cfg.trust.source.gid;
          ExecStart = "${cfg.trust.externalExecutables.source-sensor.path} serve";
          StandardInput = "socket";
          StandardOutput = "socket";
          BindReadOnlyPaths = registryHomes;
          ReadOnlyPaths = registryHomes;
          CapabilityBoundingSet = "";
        };
      };

      systemd.sockets.phase-b-custody-reader = {
        description = "Dormant Phase B custody reader socket";
        unitConfig.StopWhenUnneeded = true;
        socketConfig = {
          ListenStream = "/run/phase-b/custody-reader.sock";
          SocketMode = "0600";
          RemoveOnStop = true;
        };
      };
      systemd.services.phase-b-custody-reader = {
        description = "Isolated Phase B custody reader";
        partOf = [ "phase-b-custody-reader.socket" ];
        serviceConfig = commonHardening // {
          Type = "simple";
          DynamicUser = true;
          User = "phase-b-custody-reader";
          Group = "phase-b-custody-reader";
          ExecStart = "${cfg.trust.externalExecutables.custody-reader.path} serve";
          StandardInput = "socket";
          StandardOutput = "socket";
          InaccessiblePaths = [
            "/var/lib/phase-b"
            "/etc/phase-b"
            "/home"
            "/root"
          ];
          RestrictAddressFamilies = [
            "AF_UNIX"
            "AF_INET"
            "AF_INET6"
          ];
          IPAddressAllow = cfg.custodyReaderIPAddressAllow;
          CapabilityBoundingSet = "";
        };
      };
    })
  ];
}
