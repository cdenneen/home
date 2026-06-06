{ writeShellScriptBin }:
writeShellScriptBin "deploy-jarvis" (
  builtins.readFile ../modules/hm/users/cdenneen/files/deploy-jarvis
)
