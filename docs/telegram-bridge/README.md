# OpenCode Telegram Bridge

This repo provides the OpenCode <-> Telegram bridge as a package, a Home Manager module, and a NixOS module.

## Package usage (direct)

```sh
nix build .#opencode-telegram-bridge
./result/bin/opencode-telegram-bridge --help
```

## Overlay usage

```nix
{
  inputs.home.url = "github:cdenneen/home";

  outputs = { home, nixpkgs, ... }: {
    overlays.default = final: prev: {
      opencode-telegram-bridge = home.packages.${prev.stdenv.hostPlatform.system}.opencode-telegram-bridge;
    };
  };
}
```

## Home Manager module

```nix
{
  inputs.home.url = "github:cdenneen/home";

  outputs = { home, ... }: {
    homeConfigurations.myuser = home.lib.homeManagerConfiguration {
      modules = [
        home.homeModules.default
        {
          programs.telegram-bridge = {
            enable = true;
            telegram.botTokenFile = "/run/secrets/telegram_bot_token";
            telegram.ownerChatIdFile = "/run/secrets/telegram_chat_id";
            telegram.updatesMode = "webhook";
            telegram.webhook.publicUrl = "https://example.net";

            opencode.workspaceRoot = "/home/myuser/src/workspace";
            opencode.useSharedServer = true;
            opencode.serverUrl = "http://127.0.0.1:4097";

            web = {
              enable = true;
              baseUrl = "http://127.0.0.1:4097";
              forwardUserPrompts = true;
              forwardAgentSteps = true;
            };

            cloudflared = {
              enable = true;
              tokenFile = "/run/secrets/cloudflare_tunnel_token";
              configText = ''
                ingress:
                  - hostname: example.net
                    path: /telegram
                    service: http://127.0.0.1:18080
                  - service: http_status:404
              '';
            };
          };
        }
      ];
    };
  };
}
```

Notes:
- The HM module renders `~/.config/opencode-telegram-bridge/config.json`.
- User service units are only created when `programs.telegram-bridge.systemdMode = "user"` (default).

## NixOS module

```nix
{
  inputs.home.url = "github:cdenneen/home";

  outputs = { home, ... }: {
    nixosConfigurations.myhost = home.lib.nixosSystem {
      modules = [
        home.nixosModules.default
        {
          services.opencode-telegram-bridge = {
            enable = true;
            user = "myuser";
            systemdMode = "user"; # or "system"
            enableLinger = true;  # only matters for user mode
          };

          home-manager.users.myuser.programs.telegram-bridge = {
            enable = true;
            telegram.botTokenFile = "/run/secrets/telegram_bot_token";
            telegram.ownerChatIdFile = "/run/secrets/telegram_chat_id";
            telegram.updatesMode = "webhook";
            telegram.webhook.publicUrl = "https://example.net";
            opencode.workspaceRoot = "/home/myuser/src/workspace";
            opencode.useSharedServer = true;
            opencode.serverUrl = "http://127.0.0.1:4097";
            cloudflared.enable = true;
            cloudflared.tokenFile = "/run/secrets/cloudflare_tunnel_token";
            cloudflared.configText = ''
              ingress:
                - hostname: example.net
                  path: /telegram
                  service: http://127.0.0.1:18080
                - service: http_status:404
            '';
          };
        }
      ];
    };
  };
}
```

Notes:
- `systemdMode = "user"` uses Home Manager user services; set `enableLinger = true` to run at boot.
- `systemdMode = "system"` creates system services (runs as the configured `user`).

## nix-darwin

```nix
{
  inputs.home.url = "github:cdenneen/home";

  outputs = { home, ... }: {
    darwinConfigurations.myhost = home.lib.darwinSystem {
      modules = [
        home.darwinModules.default
        {
          services.opencode-telegram-bridge = {
            enable = true;
            user = "myuser";
          };

          home-manager.users.myuser.programs.telegram-bridge = {
            enable = true;
            telegram.botTokenFile = "/run/secrets/telegram_bot_token";
            telegram.ownerChatIdFile = "/run/secrets/telegram_chat_id";
            telegram.updatesMode = "webhook";
            telegram.webhook.publicUrl = "https://example.net";
            opencode.workspaceRoot = "/Users/myuser/src/workspace";
            opencode.useSharedServer = true;
            opencode.serverUrl = "http://127.0.0.1:4097";
            cloudflared.enable = true;
            cloudflared.tokenFile = "/run/secrets/cloudflare_tunnel_token";
            cloudflared.configText = ''
              ingress:
                - hostname: example.net
                  path: /telegram
                  service: http://127.0.0.1:18080
                - service: http_status:404
            '';
          };
        }
      ];
    };
  };
}
```

## Nyx (current wiring)

`hosts/nixos/nyx.nix` sets:

- `services.opencode-telegram-bridge` to user mode with linger
- `home-manager.users.cdenneen.programs.telegram-bridge` with shared server URL on `127.0.0.1:4097`
- `cloudflared` tunnel config for `/telegram`
