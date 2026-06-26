{ lib, ... }:
{
  # Big Sur cannot run newer x86_64-darwin build tools from current Catppuccin
  # module paths. Keep the repo's existing catppuccin.* settings accepted on
  # mbair, but make them no-op so they do not build Catppuccin assets.
  options.catppuccin = lib.mkOption {
    type = lib.types.attrsOf lib.types.anything;
    default = { };
    description = "No-op Catppuccin compatibility options for mbair.";
  };
}
