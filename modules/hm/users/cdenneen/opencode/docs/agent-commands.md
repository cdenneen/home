# Agent Commands

## Evaluate the flake

```sh
nix flake show
```

## Pre-handoff evals (required)

```sh
nix eval --impure .#homeConfigurations.cdenneen@nyx.config.programs.telegram-bridge.enable
nix eval --impure .#homeConfigurations.cdenneen@VNJTECMBCD.config.programs.starship.settings.palette
nix eval --impure .#nixosConfigurations.nyx.config.home-manager.users.cdenneen.programs.telegram-bridge.enable
```

## Build a single thing (fast iteration)

```sh
nix build .#checks.aarch64-darwin.<check>
nix build .#checks.aarch64-linux.<check>
```

List check names:

```sh
nix flake show | sed -n '/checks/,$p'
```

Build one host system output:

```sh
nix build .#darwinConfigurations.<host>.system
nix build .#nixosConfigurations.<host>.config.system.build.toplevel
```

## Switch (only on the target machine)

```sh
sudo darwin-rebuild switch --flake .
sudo nixos-rebuild switch --flake .
```

## Home Manager

```sh
home-manager switch --flake .#cdenneen@<host>
```

## Bootstrap with a minimal flake

```nix
{
  inputs.home.url = "github:cdenneen/home";

  outputs = { home, ... }:
    let
      host = "foobar";
    in
    home.lib.bootstrap {
      hostName = host;
      kind = "nixos"; # nixos or darwin
      system = "x86_64-linux";
      tags = [ "crostini" ];
      users = [ "cdenneen" ];
      nixosModules = [ ./configuration.nix ];
    };
}
```

## Formatting / lint

```sh
nix fmt
```

If you need the wrapper directly:

```sh
nix develop -c treefmt
nix develop -c treefmt --check
```

Rule: run `nix fmt` before committing.

## “Single test” equivalents

- One check: `nix build .#checks.<system>.<check>`
- One host: `nix build .#(darwin|nixos)Configurations.<host>...`
