let
  mkHostMap =
    hosts:
    builtins.listToAttrs (
      map (host: {
        name = host.name;
        value = host;
      }) hosts
    );

  nixos = [
    {
      name = "eros";
      system = "aarch64-linux";
      modules = [ ./nixos/eros.nix ];
      homeModules = [ ./nixos/eros-home.nix ];
      tags = [ "ec2" ];
    }
    {
      name = "eros-ec2";
      system = "aarch64-linux";
      modules = [ ./nixos/eros-ec2.nix ];
      tags = [ "ec2" ];
    }
    {
      name = "amazon-ami";
      system = "aarch64-linux";
      modules = [ ./nixos/amazon-ami.nix ];
      tags = [
        "ec2"
        "amazon-ami"
      ];
    }
    {
      name = "nyx";
      system = "aarch64-linux";
      modules = [
        ./nixos/nyx.nix
        ./nixos/nyx-alpha0-node.nix
        ./nixos/nyx-alpha0-gitlab-relay.nix
      ];
      homeModules = [ ./nixos/nyx-home.nix ];
      tags = [ "ec2" ];
      # hermes-agent's Nix build ships a sealed venv with no pip; provider
      # SDKs upstream normally lazy-pip-installs on demand (tools/lazy_deps.py)
      # have to be baked in at build time instead. Bedrock needs `anthropic`
      # for its Converse-API client. Must come from unstablePkgs specifically
      # (matches hermes-src's own nixpkgs.follows = "nixpkgs-unstable" in
      # flake.nix) - a mismatched revision makes python312.pkgs.requiredPythonModules
      # silently drop the package instead of erroring. Scoped to nyx only via
      # this per-host hook (see mkNixosSystem/nixos.nix); other hosts get the
      # identity default and are unaffected.
      #
      # Bumped to 0.122.0 (latest on PyPI as of this writing) - nixpkgs-unstable
      # only had 0.109.1 pinned. Same core dependency set in both versions
      # (checked via PyPI metadata), so the collision-avoidance filter below
      # still applies unchanged.
      #
      # doCheck disabled: anthropic's test suite pulls in scipy as a
      # transitive test dependency, and scipy's own test suite has an
      # unrelated flaky Hypothesis-based statistics test that fails on
      # aarch64. We only consume anthropic at runtime; its tests never run in
      # this build either way, so there's no coverage lost.
      #
      # dependencies pruned to just docstring-parser: every other runtime dep
      # anthropic declares (anyio, distro, httpx, jiter, pydantic, sniffio,
      # typing-extensions) already exists in hermes's sealed venv at versions
      # well inside anthropic's stated constraints - propagating our own
      # copies trips hermes-agent.nix's plugin/core collision guard (ambiguous
      # PYTHONPATH shadowing), so we let the sealed venv's own copies satisfy
      # those imports and only add what's actually missing.
      agentPkgsOverride =
        agentPkgs: unstablePkgs:
        let
          pyPkgs = unstablePkgs.python312Packages;
          # Drop-and-skip-checks helper: strip a package's own `dependencies`
          # down to `keep`, and disable the two build-time consistency checks
          # (runtime-deps-check, imports-check) that compare this derivation's
          # inputs against its wheel's METADATA - both would otherwise flag
          # the deps we're intentionally NOT propagating (they're satisfied
          # by hermes's already-sealed venv at actual runtime instead).
          pruneDeps =
            pkg: keep:
            pkg.overridePythonAttrs (old: {
              dependencies = builtins.filter (p: builtins.elem (p.pname or p.name) keep) old.dependencies;
              dontCheckRuntimeDeps = true;
              pythonImportsCheck = [ ];
              # Each package's own test suite (doCheck) assumes its full,
              # unpruned dependency set is present in this isolated build -
              # it isn't, on purpose. We only consume these at runtime inside
              # hermes's already-sealed venv; their tests never ran as part
              # of that build either way.
              doCheck = false;
            });

          # anthropic: bumped to 0.122.0 (latest on PyPI; nixpkgs-unstable
          # only had 0.109.1) - same core dependency set in both versions, so
          # the prune below covers both. Every runtime dep it declares except
          # docstring-parser (anyio, distro, httpx, jiter, pydantic, sniffio,
          # typing-extensions) already exists in hermes's sealed venv at
          # versions inside anthropic's stated constraints.
          # doCheck disabled separately: anthropic's own test suite pulls in
          # scipy as a transitive test dep, and scipy's test suite has an
          # unrelated flaky Hypothesis statistics test that fails on
          # aarch64 - we only consume anthropic at runtime, its tests never
          # run in this build either way.
          patchedAnthropic = pruneDeps (pyPkgs.anthropic.overridePythonAttrs (_old: {
            version = "0.122.0";
            src = unstablePkgs.fetchFromGitHub {
              owner = "anthropics";
              repo = "anthropic-sdk-python";
              rev = "v0.122.0";
              sha256 = "1f5bl3mb30r1xh8zzjrbk7sqgkjdkkhbasljy9pgd7drm4a4795g";
            };
          })) [ "docstring-parser" ];

          # boto3/botocore: needed by hermes's own Bedrock provider for AWS
          # credential resolution (separate from anthropic's optional
          # `[bedrock]` extra). botocore's own python-dateutil and urllib3
          # already exist in the sealed venv too - same collision, same fix,
          # threaded through s3transfer/boto3 so they reference the pruned
          # botocore rather than nixpkgs' unpatched one.
          patchedBotocore = pruneDeps pyPkgs.botocore [ "jmespath" ];
          patchedS3transfer = (pruneDeps pyPkgs.s3transfer [ ]).overridePythonAttrs (_old: {
            dependencies = [ patchedBotocore ];
          });
          patchedBoto3 = (pruneDeps pyPkgs.boto3 [ ]).overridePythonAttrs (_old: {
            dependencies = [
              patchedBotocore
              pyPkgs.jmespath
              patchedS3transfer
            ];
          });
        in
        agentPkgs
        // {
          hermes = agentPkgs.hermes.override {
            extraPythonPackages = [
              patchedAnthropic
              patchedBoto3
            ];
          };
        };
    }
    {
      name = "MacBook-Pro-NixOS";
      system = "x86_64-linux";
      modules = [ ./nixos/MacBook-Pro-NixOS.nix ];
      homeModules = [ ./nixos/MacBook-Pro-NixOS-home.nix ];
      tags = [ ];
    }
    {
      name = "ghost";
      system = "aarch64-linux";
      modules = [ ./nixos/ghost.nix ];
      homeModules = [ ./nixos/ghost-home.nix ];
      tags = [ "qemu-guest" ];
    }
    {
      name = "ghost-bootstrap";
      system = "aarch64-linux";
      modules = [ ./nixos/ghost-bootstrap.nix ];
      tags = [ "qemu-guest" ];
    }
    {
      name = "savage";
      system = "x86_64-linux";
      modules = [ ./nixos/savage.nix ];
      tags = [ ];
    }
    {
      name = "flash";
      system = "x86_64-linux";
      modules = [ ./nixos/flash.nix ];
      tags = [ ];
    }
    {
      name = "onyx";
      system = "x86_64-linux";
      modules = [ ./nixos/onyx.nix ];
      tags = [ "qemu-guest" ];
    }
    {
      name = "talon";
      system = "x86_64-linux";
      modules = [ ./nixos/talon.nix ];
      tags = [ "qemu-guest" ];
    }
    {
      name = "utm";
      system = "aarch64-linux";
      modules = [ ./nixos/utm.nix ];
      tags = [ "qemu-guest" ];
    }
    {
      name = "wsl";
      system = "x86_64-linux";
      modules = [ ./nixos/wsl.nix ];
      homeModules = [ ./nixos/wsl-home.nix ];
      tags = [ "wsl" ];
    }
  ];

  darwin = [
    {
      name = "VNJTECMBCD";
      system = "aarch64-darwin";
      modules = [ ./darwin/VNJTECMBCD.nix ];
      homeModules = [ ./darwin/VNJTECMBCD-home.nix ];
      tags = [ ];
    }
    {
      name = "mbair";
      system = "x86_64-darwin";
      modules = [ ./darwin/mbair.nix ];
      homeModules = [ ./darwin/mbair-home.nix ];
      legacyBigSur = true;
      tags = [ ];
    }
  ];

  all = nixos ++ darwin;
in
{
  inherit nixos darwin all;

  nixosByName = mkHostMap nixos;
  darwinByName = mkHostMap darwin;
  allByName = mkHostMap all;

  hostsByKind = {
    nixos = nixos;
    darwin = darwin;
  };

  hostNames = {
    nixos = map (h: h.name) nixos;
    darwin = map (h: h.name) darwin;
  };
}
