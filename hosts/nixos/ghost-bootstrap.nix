{ ... }:
{
  imports = [ ./ghost-base.nix ];

  users.users.root.openssh.authorizedKeys.keyFiles = [
    ../../pub/ssh/cdenneen_ed25519_2024.pub
  ];
}
