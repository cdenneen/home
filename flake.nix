{
  description = "Collin Diekvoss Nix Configurations";

  nixConfig = {
    trusted-users = [
      "root"
      "cdenneen"
    ];
    extra-substituters = [
      "https://cache.nixos.org"
      "https://nix-community.cachix.org"
      "https://cdenneen.cachix.org"
    ];
    extra-trusted-public-keys = [
      "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
      "nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs="
      "cdenneen.cachix.org-1:EUognwSf1y0FAzDOPmUuYtz6aOxCWyNbcMi8PjHV8gU="
    ];
  };

  inputs = {
    # Canonical nixpkgs input required by flake-parts (stable for system builds)
    nixpkgs.url = "github:nixos/nixpkgs/nixos-26.05";
    arion = {
      url = "github:hercules-ci/arion";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    catppuccin.url = "github:catppuccin/nix";
    devshell = {
      url = "github:numtide/devshell";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    flake-parts.url = "github:hercules-ci/flake-parts";
    home-manager = {
      url = "github:nix-community/home-manager/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    home-manager-mbair = {
      url = "github:nix-community/home-manager/release-25.05";
      inputs.nixpkgs.follows = "nixpkgs-mbair";
    };
    apple-silicon-support.url = "github:nix-community/nixos-apple-silicon";
    axis = {
      url = "git+https://gitlab.com/ghostspace/axis.git";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    axis-control = {
      url = "github:ghostspace-com/axis-control/main";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.home-manager.follows = "home-manager";
    };
    axis-mbair = {
      url = "git+https://gitlab.com/ghostspace/axis.git?rev=caab5254bbd8a9d01705f53c4e88e416139b05e7";
      inputs.nixpkgs.follows = "nixpkgs-mbair";
    };
    nixos-crostini.url = "github:aldur/nixos-crostini";
    mac-app-util.url = "github:hraban/mac-app-util";
    nix-darwin = {
      url = "github:nix-darwin/nix-darwin/nix-darwin-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-darwin-mbair = {
      url = "github:nix-darwin/nix-darwin/nix-darwin-25.05";
      inputs.nixpkgs.follows = "nixpkgs-mbair";
    };
    nix-index-database = {
      url = "github:nix-community/nix-index-database";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    opnix.url = "github:brizzbuzz/opnix";
    nixos-hardware.url = "github:nixos/nixos-hardware";
    nixos-wsl.url = "github:nix-community/nixos-wsl";
    nixpkgs-stable.follows = "nixpkgs";
    nixpkgs-mbair.url = "github:nixos/nixpkgs/nixos-25.05";
    nixpkgs-unstable.url = "github:nixos/nixpkgs/nixos-unstable";
    opencode-src = {
      url = "github:sst/opencode";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    codex-src = {
      url = "github:sadjow/codex-cli-nix";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    claude-src = {
      url = "github:sadjow/claude-code-nix";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    ponytail-src = {
      url = "github:DietrichGebert/ponytail/16f29800fd2681bdf24f3eb4ccffe38be3baec6b";
      flake = false;
    };
    hermes-src = {
      url = "github:NousResearch/hermes-agent/f5be9236e00ddf2f2a412697f267078fc4ee068e";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    alpha0 = {
      url = "github:ghostspace-com/alpha0/c6dc926e8e3622ca5f9e9ac6f3dbc78cf43c9254";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.home-manager.follows = "home-manager";
      inputs.hermes-src.follows = "hermes-src";
    };
    fluxcdAgentSkills = {
      url = "github:cdenneen/fluxcd-agent-skills";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    greptileSkills = {
      url = "github:greptileai/skills";
      flake = false;
    };
    nur.url = "github:nix-community/nur";
    vimnix = {
      url = "github:cdenneen/vimnix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.home-manager.follows = "home-manager";
    };
    nix-homebrew.url = "github:zhaofengli/nix-homebrew";
    nix-homebrew-mbair.url = "github:zhaofengli/nix-homebrew/0406ffd7d3a4e285b618a226a837f4fe9b1a36b7";
    homebrew-taps-mbair = {
      url = "github:cdenneen/homebrew-taps/e70bd4ea4ead556d801227946b9e33131d273466";
      flake = false;
    };
    sops-nix.url = "github:Mic92/sops-nix";
    sops-nix-mbair = {
      url = "github:Mic92/sops-nix/17eea6f3816ba6568b8c81db8a4e6ca438b30b7c";
      inputs.nixpkgs.follows = "nixpkgs-mbair";
    };
    treefmt-nix.url = "github:numtide/treefmt-nix";
  };

  outputs =
    inputs@{
      apple-silicon-support,
      devshell,
      flake-parts,
      nixos-crostini,
      nur,
      self,
      treefmt-nix,
      ...
    }:
    let
      configurations = import ./systems (inputs // { inherit self; });
      import_nixpkgs_with =
        system: nixpkgs:
        {
          darwinMinVersion ? null,
          includeNur ? true,
        }:
        import nixpkgs {
          localSystem = {
            inherit system;
          }
          // nixpkgs.lib.optionalAttrs (darwinMinVersion != null) {
            inherit darwinMinVersion;
          };
          overlays = [
            (final: prev: {
              # nixpkgs kept nixfmt-rfc-style as an alias for a while and emits a warning
              # when it is evaluated. Some tooling (eg treefmt-nix defaults) still refers
              # to that name. Override it to the canonical package to avoid the warning.
              "nixfmt-rfc-style" = prev.nixfmt;

              # Avoid deprecation warning from xorg.lndir alias.
              xorg = prev.xorg // prev.lib.optionalAttrs (prev ? lndir) { lndir = prev.lndir; };

              # inetutils fails on darwin with -Werror=format-security.
              inetutils = prev.inetutils.overrideAttrs (old: {
                NIX_CFLAGS_COMPILE =
                  (old.NIX_CFLAGS_COMPILE or [ ])
                  ++ prev.lib.optionals prev.stdenv.isDarwin [
                    "-Wno-error=format-security"
                    "-Wno-format-security"
                  ];
              });

              # vimnix expects rust-analyzer-nightly; fall back to rust-analyzer.
              rust-analyzer-nightly =
                if prev ? rust-analyzer-nightly then prev.rust-analyzer-nightly else prev.rust-analyzer;

              # direnv 2.37.x on darwin builds with `-linkmode=external`,
              # which requires cgo. Force cgo for this package until nixpkgs
              # ships a fixed expression.
              direnv = prev.direnv.overrideAttrs (
                old:
                prev.lib.optionalAttrs prev.stdenv.isDarwin {
                  env = (old.env or { }) // {
                    CGO_ENABLED = "1";
                  };
                }
              );

              # oauth2-proxy 7.15.3 requires Go 1.26, but nixpkgs still uses
              # the default Go 1.25 builder for this package.
              oauth2-proxy =
                if prev.oauth2-proxy.version == "7.15.3" && prev.lib.versionOlder prev.go.version "1.26" then
                  prev.oauth2-proxy.override {
                    buildGoModule = prev.buildGo126Module;
                  }
                else
                  prev.oauth2-proxy;

            })
          ]
          ++ nixpkgs.lib.optional includeNur nur.overlays.default;
          config = {
            allowBroken = true;
            allowUnfree = true;
          };
        };
      import_nixpkgs = system: nixpkgs: import_nixpkgs_with system nixpkgs { };
    in
    flake-parts.lib.mkFlake { inherit inputs; } {
      flake = {
        lib = {
          inherit import_nixpkgs import_nixpkgs_with;
        }
        // configurations.lib;
        nixosModules.default = ./modules/system/nixos;
        darwinModules.default = ./modules/system/darwin;
        homeModules.default = ./modules/hm;
        nixosConfigurations = configurations.nixosConfigurations;
        darwinConfigurations = configurations.darwinConfigurations;
        homeConfigurations = configurations.homeConfigurations;
      };
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      imports = [
        devshell.flakeModule
        flake-parts.flakeModules.easyOverlay
        treefmt-nix.flakeModule
      ];
      perSystem =
        {
          config,
          pkgs,
          lib,
          system,
          self',
          ...
        }:
        let
          direnvForShell =
            if pkgs.stdenv.isDarwin then
              pkgs.direnv.overrideAttrs (old: {
                env = (old.env or { }) // {
                  CGO_ENABLED = "1";
                };
              })
            else
              pkgs.direnv;
        in
        {
          # Let flake-parts provide pkgs; do not override to avoid recursion
          # Let flake-parts manage pkgs; do not override with a manual nixpkgs import

          treefmt = {
            programs = {
              nixfmt = {
                enable = true;
                # Avoid nixpkgs warning about nixfmt-rfc-style aliasing.
                package = pkgs.nixfmt;
              };
              prettier.enable = true;
            };

            # Do not let formatters rewrite encrypted SOPS files.
            settings.global.excludes = [
              "secrets/secrets.yaml"
              "secrets/ghost.yaml"
              "secrets/axis.yaml"
              "secrets/jarvis.yaml"
            ];

            settings.formatter.prettier.excludes = [
              "secrets/secrets.yaml"
              "secrets/ghost.yaml"
              "secrets/axis.yaml"
              "secrets/jarvis.yaml"
            ];
          };

          # Make `nix fmt` work and provide a `treefmt` wrapper.
          formatter = config.treefmt.build.wrapper;

          packages = {
            treefmt = config.treefmt.build.wrapper;
            setup-sops = pkgs.callPackage ./pkgs/setup-sops.nix { };
            setup-git-sops = pkgs.callPackage ./pkgs/setup-git-sops.nix { };
            git-sops = pkgs.callPackage ./pkgs/git-sops.nix { };
            pre-commit = pkgs.callPackage ./pkgs/pre-commit.nix { };
            sops-edit = pkgs.callPackage ./pkgs/sops-edit.nix { };
            sops-update-keys = pkgs.callPackage ./pkgs/sops-update-keys.nix { };
            sops-check = pkgs.callPackage ./pkgs/sops-check.nix { };
            sops-diff-keys = pkgs.callPackage ./pkgs/sops-diff-keys.nix { };
            sops-verify-keys = pkgs.callPackage ./pkgs/sops-verify-keys.nix { };
            sops-bootstrap-host = pkgs.callPackage ./pkgs/sops-bootstrap-host.nix { };
            update-workspace-agents = pkgs.callPackage ./pkgs/update-workspace-agents.nix { };
            workspace-init = pkgs.callPackage ./pkgs/workspace-init.nix { };
            setup-repo = pkgs.callPackage ./pkgs/setup-repo.nix { };
            update-workspace = pkgs.callPackage ./pkgs/update-workspace.nix { };
            tokensave = pkgs.callPackage ./pkgs/tokensave.nix { };
            pi-agent = pkgs.callPackage ./pkgs/pi-agent.nix { };
            pi-plugins = pkgs.callPackage ./pkgs/pi-plugins.nix { };
          }
          // pkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
            phase-b-tooling = pkgs.callPackage ./pkgs/phase-b-tooling.nix { };
          };

          devshells.default = {
            # Align devshell tooling with system/Home Manager pkgs
            packages = [
              pkgs.git
              pkgs.ripgrep
              direnvForShell
              pkgs.fzf
              pkgs.eza
              self'.packages.treefmt
            ];
            commands = [
              {
                package = self'.packages.setup-sops;
              }
              {
                package = self'.packages.setup-git-sops;
              }
              {
                package = self'.packages.sops-edit;
              }
              {
                package = self'.packages.sops-update-keys;
              }
              {
                package = self'.packages.sops-check;
              }
              {
                package = self'.packages.sops-diff-keys;
              }
              {
                package = self'.packages.sops-verify-keys;
              }
              {
                package = self'.packages.sops-bootstrap-host;
              }
            ];
            imports = [ "${devshell}/extra/git/hooks.nix" ];
            git.hooks = {
              enable = true;
              pre-commit.text = self'.packages.pre-commit.text;
            };
          };

          # Keep flake checks fast and pure. Full host eval/build targets are
          # executed explicitly in CI workflow jobs.
          checks =
            with pkgs.lib;
            let
              isCacheable = v: isDerivation v;
            in
            mapAttrs' (n: nameValuePair "devShells-${n}") (filterAttrs (n: v: isCacheable v) self'.devShells)
            // {
              hermes-slack-platform =
                pkgs.runCommand "hermes-slack-platform-check"
                  {
                    nativeBuildInputs = [
                      (pkgs.python3.withPackages (pythonPackages: [ pythonPackages.pytest ]))
                      pkgs.ruff
                    ];
                  }
                  ''
                    cp -R ${./modules/hm/users/cdenneen/hermes-slack-platform} source
                    chmod -R u+w source
                    ruff check source/plugin source/tests
                    pytest -q source/tests
                    touch "$out"
                  '';
              hermes-supervisor =
                pkgs.runCommand "hermes-supervisor-check"
                  {
                    nativeBuildInputs = [
                      pkgs.check-jsonschema
                      pkgs.git
                      pkgs.pyright
                      (pkgs.python3.withPackages (pythonPackages: [
                        pythonPackages.jsonschema
                        pythonPackages.pytest
                      ]))
                      pkgs.ruff
                    ];
                  }
                  ''
                    cp -R ${./modules/hm/users/cdenneen/hermes-supervisor} source
                    chmod -R u+w source
                    ruff check source/scripts source/tests
                    pytest -q source/tests
                    pyright --project source/pyrightconfig.json
                    check-jsonschema --check-metaschema source/schemas/*.schema.json
                    check-jsonschema --schemafile source/schemas/control.schema.json source/control.defaults.json
                    touch "$out"
                  '';
              phase-b-tooling-static =
                pkgs.runCommand "phase-b-tooling-static-check"
                  {
                    nativeBuildInputs = [
                      pkgs.check-jsonschema
                      pkgs.pyright
                      pkgs.ruff
                    ];
                  }
                  ''
                    cp -R ${./pkgs/phase-b-tooling} source
                    chmod -R u+w source
                    ruff check --ignore E402 source
                    pyright source
                    check-jsonschema --check-metaschema source/phase_b/schemas/*.schema.json
                    cat > consumption-refresh.json <<'EOF'
                    {"attempt_id":"attempt","authorization_grant_digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","consumer_identity":"consumer","consumer_nonce":"nonce","continued_at":"2026-08-20T00:00:00Z","expected_counter":1,"grant_expires_at":"2026-08-20T00:15:00Z","previous_receipt_digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","receipt_digest":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","receiver_head":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","requested_transition":"PHASE_B_FENCING_QUALIFICATION","schema":"phase-b.consumption.v1"}
                    EOF
                    check-jsonschema --schemafile source/phase_b/schemas/consumption.schema.json consumption-refresh.json
                    touch "$out"
                  '';
              hermes-watchdog =
                pkgs.runCommand "hermes-watchdog-check"
                  {
                    nativeBuildInputs = [
                      pkgs.check-jsonschema
                      pkgs.pyright
                      (pkgs.python3.withPackages (pythonPackages: [
                        pythonPackages.jsonschema
                        pythonPackages.pytest
                      ]))
                      pkgs.ruff
                    ];
                  }
                  ''
                    cp -R ${./modules/hm/users/cdenneen/hermes-watchdog} source
                    cp -R ${./modules/hm/users/cdenneen/hermes-supervisor} hermes-supervisor
                    chmod -R u+w source hermes-supervisor
                    export PYTHONPATH="$PWD/source/scripts"
                    ruff check source/scripts source/tests
                    pytest -q source/tests
                    pyright --project source/pyrightconfig.json
                    check-jsonschema --check-metaschema source/schemas/*.schema.json
                    check-jsonschema --schemafile source/schemas/control.schema.json source/control.defaults.json
                    touch "$out"
                  '';
            }
            // optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
              phase-b-tooling-unit = self'.packages.phase-b-tooling;
              phase-b-tooling-module = pkgs.callPackage ./pkgs/phase-b-tooling/module-eval-check.nix {
                module = ./modules/system/nixos/phase-b-tooling.nix;
                nixosSystem = inputs.nixpkgs.lib.nixosSystem;
                phaseB = self'.packages.phase-b-tooling;
              };
              phase-b-tooling-vm = pkgs.callPackage ./pkgs/phase-b-tooling/vm-test.nix {
                module = ./modules/system/nixos/phase-b-tooling.nix;
                phaseB = self'.packages.phase-b-tooling;
              };
              axis-control-package = inputs.axis-control.checks.${system}.package;
              axis-control-home-module = inputs.axis-control.checks.${system}.home-module;
              axis-slack-ingress =
                let
                  ghost = configurations.nixosConfigurations.ghost.config;
                  proxyCommand = ghost.systemd.services.axis-api-auth-proxy.serviceConfig.ExecStart;
                  proxy = builtins.elemAt (splitString " " proxyCommand) 1;
                  axisPreStart = ghost.systemd.services.axis.preStart;
                  axisCapabilitySetup = builtins.readFile (builtins.head (splitString "\n" axisPreStart));
                in
                assert inputs.axis.rev == "40b28f398754c316eae7027d6ae50f218c9f727c";
                assert hasPrefix "/nix/store/" proxy;
                assert hasInfix "--secret-name provider.slack.identity.454f27f29c5964c6be1bf84bec9176ef"
                  axisCapabilitySetup;
                assert !(hasInfix "provider.slack.identity.U0B7ZGP6M43" axisCapabilitySetup);
                assert hasInfix "canonical profile database is not readable" axisCapabilitySetup;
                assert hasInfix "active principal is not the deployment owner" axisCapabilitySetup;
                pkgs.runCommand "axis-slack-ingress-check" { nativeBuildInputs = [ pkgs.jq pkgs.python3 ]; } ''
                  if ${pkgs.jq}/bin/jq -e --arg principal_id principal.not-owner '
                    .principals
                    | select(type == "array")
                    | any(
                        .[];
                        type == "object"
                        and .principal_id == $principal_id
                        and .relationship == "owner"
                        and (.role_ids | if type == "array" then index("deployment-owner") != null else false end)
                      )
                  ' > /dev/null <<'JSON'
                  {"principals":[{"principal_id":"principal.not-owner","relationship":"collaborator","role_ids":["deployment-owner"]}]}
                  JSON
                  then
                    echo "non-owner AXIS context passed Slack identity authorization" >&2
                    exit 1
                  fi
                  ${pkgs.gnused}/bin/sed \
                    -e 's/"127.0.0.1", 8780/"127.0.0.1", 18780/' \
                    -e 's/("127.0.0.1", 8001)/("127.0.0.1", 18001)/' \
                    -e 's|TOKEN_FILE = ".*"|TOKEN_FILE = "axis-api-auth-proxy-token"|' \
                    ${proxy} > axis-api-auth-proxy.py
                  ${pkgs.coreutils}/bin/printf 'test-token' > axis-api-auth-proxy-token
                  ${pkgs.python3}/bin/python axis-api-auth-proxy.py &
                  proxy_pid=$!
                  trap '${pkgs.coreutils}/bin/kill "$proxy_pid"' EXIT
                  ${pkgs.python3}/bin/python - <<'PY'
                  import http.client
                  import threading
                  import time
                  from http.server import BaseHTTPRequestHandler, HTTPServer

                  received = {}

                  class Upstream(BaseHTTPRequestHandler):
                      def do_POST(self):
                          received["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                          received["signature"] = self.headers["X-Slack-Signature"]
                          received["timestamp"] = self.headers["X-Slack-Request-Timestamp"]
                          received["content_type"] = self.headers["Content-Type"]
                          self.send_response(200)
                          self.end_headers()

                      def log_message(self, format, *args):
                          return

                  upstream = HTTPServer(("127.0.0.1", 18780), Upstream)
                  threading.Thread(target=upstream.serve_forever, daemon=True).start()

                  def request(method, path, body=None, **headers):
                      for _ in range(20):
                          try:
                              connection = http.client.HTTPConnection("127.0.0.1", 18001, timeout=1)
                              connection.request(method, path, body=body, headers={"Host": "slack.denneen.net", **headers})
                              status = connection.getresponse().status
                              connection.close()
                              return status
                          except ConnectionRefusedError:
                              time.sleep(0.1)
                      raise AssertionError("AXIS API proxy did not start")

                  assert request("POST", "/") == 404
                  assert request("GET", "/callbacks/slack") == 405
                  assert request("POST", "/callbacks/slack?unexpected=query") == 404
                  assert request("OPTIONS", "/callbacks/slack") == 405
                  assert request("POST", "/api/health", Host="axis.denneen.net") == 401
                  assert request("POST", "/callbacks/slack", **{"Content-Length": "1048577"}) == 413
                  assert request(
                      "POST",
                      "/callbacks/slack",
                      body=b"{}",
                      **{
                          "Content-Type": "application/json",
                          "X-Slack-Request-Timestamp": "1",
                          "X-Slack-Signature": "v0=test",
                      },
                  ) == 200
                  assert received == {
                      "body": b"{}",
                      "content_type": "application/json",
                      "signature": "v0=test",
                      "timestamp": "1",
                  }
                  upstream.shutdown()
                  PY
                  ${pkgs.coreutils}/bin/kill "$proxy_pid"
                  wait "$proxy_pid" || true
                  trap - EXIT
                  touch "$out"
                '';
              hermes-gateway-roles =
                let
                  ghost = configurations.homeConfigurations."cdenneen@ghost".config;
                  nyx = configurations.homeConfigurations."cdenneen@nyx".config;
                  nyxIntegrated = configurations.nixosConfigurations.nyx.config.home-manager.users.cdenneen;
                  userSystemd = ghost.systemd.user;
                  services = userSystemd.services;
                  timers = userSystemd.timers;
                  primary = services.hermes-gateway.Service;
                  axisControlPackage = ghost.services.axis-control-observer.package;
                  axisControlWatchdog = services.axis-control-watchdog;
                  watchdogCommand = builtins.concatStringsSep "\n" axisControlWatchdog.Service.ExecStart;
                  profileWrapperActivation = ghost.home.activation.axisControlProfileWrapper.data;
                  gatewayBootstrapActivation = ghost.home.activation.hermesGatewayBootstrapConfig.data;
                  slackPluginActivation = ghost.home.activation.hermesSlackPlatformConfig.data;
                  primaryCommand = builtins.concatStringsSep "\n" primary.ExecStart;
                  nyxPrimaryCommand = builtins.concatStringsSep "\n" (
                    nyx.systemd.user.services.hermes-gateway.Service.ExecStart
                  );
                  nyxIntegratedPrimaryCommand = builtins.concatStringsSep "\n" (
                    nyxIntegrated.systemd.user.services.hermes-gateway.Service.ExecStart
                  );
                  slackPluginTargets = [
                    ghost.home.file.".hermes/plugins/platforms/slack".source
                    ghost.home.file.".hermes/profiles/axis-control/plugins/platforms/slack".source
                    ghost.home.file.".local/share/alpha0/hermes/plugins/platforms/slack".source
                    ghost.home.file."src/workspace/work/axis-control/.hermes/profiles/axis-control/plugins/platforms/slack".source
                    nyx.home.file.".hermes/plugins/platforms/slack".source
                    nyx.home.file.".hermes/profiles/nyx-gitlab/plugins/platforms/slack".source
                  ];
                in
                assert inputs.axis-control.rev == "7916fd0f92e6b6198661e85ab74e742384df0700";
                assert inputs.alpha0.rev == "c6dc926e8e3622ca5f9e9ac6f3dbc78cf43c9254";
                assert userSystemd.startServices == false;
                assert primary.WorkingDirectory == "%h/.hermes";
                assert builtins.elem "HERMES_HOME=%h/.hermes" primary.Environment;
                assert primary.EnvironmentFile == [ "%h/.hermes/.env" ];
                assert lib.hasInfix
                  ''if [ ! -e "$HOME/.hermes/config.yaml" ] && [ ! -L "$HOME/.hermes/config.yaml" ]; then''
                  gatewayBootstrapActivation;
                assert lib.hasInfix ''"$HOME/.hermes/config.yaml"'' gatewayBootstrapActivation;
                assert lib.hasInfix "install -D -m 600 -T" gatewayBootstrapActivation;
                assert !(lib.hasInfix "--profile" primaryCommand);
                assert ghost.profiles.hermesAxisControlGateway.enable == false;
                assert ghost.profiles.hermesSupervisor.enable == false;
                assert ghost.services.axis-control-observer.enable;
                assert !(services ? hermes-axis-control-gateway);
                assert !(lib.hasInfix "/src/workspace/work/axis-control" (builtins.toJSON services));
                assert !(ghost.home.activation ? hermesAxisControlGatewayLegacyCleanup);
                assert !(services ? hermes-supervisor-cron);
                assert !(ghost.home.activation ? hermesSupervisorState);
                assert !(ghost.home.file ? ".hermes/supervisor/axis-development-supervisor/worker-prompt.txt");
                assert
                  axisControlWatchdog.Service.ExecStart == [
                    "${axisControlPackage}/bin/axis-control watchdog --hermes-home /home/cdenneen/.hermes --profile axis-control"
                  ];
                assert !(axisControlWatchdog ? Install);
                assert lib.hasPrefix "/nix/store/" watchdogCommand;
                assert lib.hasInfix "/nix/store/" profileWrapperActivation;
                assert !(lib.hasInfix "/src/workspace/work/axis-control" profileWrapperActivation);
                assert !(services ? axis-control-observe);
                assert !(timers ? axis-control-observe);
                assert !(timers ? axis-control-watchdog);
                assert ghost.services.alpha0.enableCore == false;
                assert ghost.services.alpha0.enableGateway == false;
                assert ghost.services.alpha0.dataHome == "/home/cdenneen/.local/share/alpha0";
                assert !(services ? alpha0-core);
                assert !(services ? hermes-alpha0-gateway);
                assert lib.all (source: source == builtins.head slackPluginTargets) slackPluginTargets;
                assert lib.all (target: target.recursive == false) [
                  ghost.home.file.".hermes/plugins/platforms/slack"
                  ghost.home.file.".hermes/profiles/axis-control/plugins/platforms/slack"
                  ghost.home.file.".local/share/alpha0/hermes/plugins/platforms/slack"
                  ghost.home.file."src/workspace/work/axis-control/.hermes/profiles/axis-control/plugins/platforms/slack"
                  nyx.home.file.".hermes/plugins/platforms/slack"
                  nyx.home.file.".hermes/profiles/nyx-gitlab/plugins/platforms/slack"
                ];
                assert lib.hasInfix "version: 1.0.2" (
                  builtins.readFile (builtins.head slackPluginTargets + "/plugin.yaml")
                );
                assert lib.all (configPath: lib.hasInfix configPath slackPluginActivation) [
                  "/home/cdenneen/.hermes/config.yaml"
                  "/home/cdenneen/.hermes/profiles/axis-control/config.yaml"
                  "/home/cdenneen/.local/share/alpha0/hermes/config.yaml"
                  "/home/cdenneen/src/workspace/work/axis-control/.hermes/profiles/axis-control/config.yaml"
                ];
                assert lib.hasInfix ".plugins.enabled" slackPluginActivation;
                assert lib.hasInfix "map(select(. != \"slack-platform\"))" slackPluginActivation;
                assert nyxPrimaryCommand == nyxIntegratedPrimaryCommand;
                pkgs.runCommand "hermes-gateway-roles-check" { nativeBuildInputs = [ pkgs.yq-go ]; } ''
                  export DRY_RUN_CMD=
                  export HOME="$TMPDIR/home"

                  ${gatewayBootstrapActivation}
                  test "$(stat -c %a "$HOME/.hermes/config.yaml")" = 600
                  test "$(yq -o=json -I=0 '.plugins.enabled' "$HOME/.hermes/config.yaml")" = '["platforms/slack"]'
                  cp "$HOME/.hermes/config.yaml" "$TMPDIR/original.yaml"
                  ${gatewayBootstrapActivation}
                  cmp "$TMPDIR/original.yaml" "$HOME/.hermes/config.yaml"

                  yq -i '
                    .plugins.enabled = ["slack-platform", "platforms/slack", "slack-platform"]
                    | .plugins.disabled = ["slack-platform", "platforms/slack", "other"]
                  ' "$HOME/.hermes/config.yaml"
                  cat > "$TMPDIR/slack-activation" <<'EOF'
                  ${slackPluginActivation}
                  EOF
                  substituteInPlace "$TMPDIR/slack-activation" --replace-fail /home/cdenneen "$HOME"
                  bash "$TMPDIR/slack-activation"
                  test "$(yq -o=json -I=0 '.plugins.enabled' "$HOME/.hermes/config.yaml")" = '["platforms/slack"]'
                  test "$(yq -o=json -I=0 '.plugins.disabled' "$HOME/.hermes/config.yaml")" = '["other"]'

                  export HOME="$TMPDIR/dangling"
                  mkdir -p "$HOME/.hermes"
                  ln -s "$HOME/missing" "$HOME/.hermes/config.yaml"
                  ${gatewayBootstrapActivation}
                  test -L "$HOME/.hermes/config.yaml"
                  touch "$out"
                '';
            };
        };
    };
}
