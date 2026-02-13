{ lib, ... }:
{
  options.services.displayManager.generic.environment = lib.mkOption {
    type = lib.types.attrsOf lib.types.str;
    default = { };
    description = "Compatibility shim for modules that set generic display manager environment.";
  };
}
