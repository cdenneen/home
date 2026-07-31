{ lib, ... }:
{
  # opencode currently isn't confirmed to work on x86_64-linux WSL; disable by
  # default to avoid host builds breaking. Re-enable once verified.
  programs.opencode.enable = lib.mkForce false;
}
