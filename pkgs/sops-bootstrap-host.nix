{ pkgs, ... }:
pkgs.writeShellApplication {
  name = "sops-bootstrap-host";
  runtimeInputs = with pkgs; [
    age
    coreutils
  ];
  text = ''
    set -euo pipefail

    keydir="/var/sops/age"
    keyfile="$keydir/keys.txt"

    ensure_group() {
      local group="$1"
      if getent group "$group" >/dev/null 2>&1; then
        return 0
      fi
      if [ "$(id -u)" -eq 0 ]; then
        groupadd "$group"
      else
        sudo groupadd "$group"
      fi
    }

    ensure_user_in_group() {
      local user="$1"
      local group="$2"

      if id -nG "$user" | tr ' ' '\n' | grep -qx "$group"; then
        return 0
      fi

      echo "Adding $user to $group group (re-login required)" >&2
      if [ "$(id -u)" -eq 0 ]; then
        usermod -aG "$group" "$user"
      else
        sudo usermod -aG "$group" "$user"
      fi
    }

    install_keyfile_from() {
      local src="$1"
      if [ ! -f "$src" ]; then
        return 1
      fi

      echo "Installing existing AGE key from $src -> $keyfile" >&2
      if [ "$(id -u)" -eq 0 ]; then
        cp -f "$src" "$keyfile"
      else
        sudo cp -f "$src" "$keyfile"
      fi
    }

    ensure_user_key_symlink() {
      local home="$1"
      local dest="$home/.config/sops/age/keys.txt"
      if [ -e "$dest" ]; then
        return 0
      fi

      echo "Linking $dest -> $keyfile" >&2
      if [ "$(id -u)" -eq 0 ]; then
        install -d -m 0700 -o "$SUDO_USER" -g "$SUDO_USER" "$home/.config/sops/age" 2>/dev/null || true
        ln -s "$keyfile" "$dest" 2>/dev/null || true
        chown -h "$SUDO_USER":"$SUDO_USER" "$dest" 2>/dev/null || true
      else
        mkdir -p "$home/.config/sops/age"
        chmod 0700 "$home/.config/sops/age"
        ln -s "$keyfile" "$dest"
      fi
    }

    # On NixOS, allow sops-nix (user) to read /var/sops/age/keys.txt via group.
    if [ "$(uname -s)" = "Linux" ]; then
      ensure_group sops
    fi

    if [ -f "$keyfile" ]; then
      echo "Host AGE key already exists at $keyfile" >&2
    else
      echo "Generating host AGE key at $keyfile" >&2
      if [ "$(id -u)" -eq 0 ]; then
        mkdir -p "$keydir"
        age-keygen -o "$keyfile"
      else
        sudo mkdir -p "$keydir"
        sudo age-keygen -o "$keyfile"
      fi
    fi

    # If the host key doesn't exist yet, try to promote an existing per-user key.
    if [ ! -s "$keyfile" ]; then
      for candidate in \
        "$HOME/.config/sops/age/keys.txt" \
        "/root/.config/sops/age/keys.txt" \
        "/etc/sops/age/keys.txt" \
        ; do
        install_keyfile_from "$candidate" && break || true
      done

      if [ ! -s "$keyfile" ]; then
        echo "No existing key found; generating new host key" >&2
        if [ "$(id -u)" -eq 0 ]; then
          mkdir -p "$keydir"
          age-keygen -o "$keyfile"
        else
          sudo mkdir -p "$keydir"
          sudo age-keygen -o "$keyfile"
        fi
      fi
    fi

    # Ensure directory + file perms (root:sops 0750, 0440) so user sops-nix can read.
    if [ "$(uname -s)" = "Linux" ]; then
      if [ "$(id -u)" -eq 0 ]; then
        mkdir -p "$keydir"
        chown root:sops "$keydir" || true
        chmod 0750 "$keydir" || true
        chown root:sops "$keyfile" || true
        chmod 0440 "$keyfile" || true
      else
        sudo mkdir -p "$keydir"
        sudo chown root:sops "$keydir" || true
        sudo chmod 0750 "$keydir" || true
        sudo chown root:sops "$keyfile" || true
        sudo chmod 0440 "$keyfile" || true
      fi

      if [ "$(id -u)" -eq 0 ]; then
        ensure_user_in_group "''${SUDO_USER:-root}" sops
      else
        ensure_user_in_group "$USER" sops
      fi
    else
      # Non-Linux: keep strict permissions.
      if [ "$(id -u)" -eq 0 ]; then
        chmod 0400 "$keyfile" || true
      else
        sudo chmod 0400 "$keyfile" || true
      fi
    fi

     if [ "$(id -u)" -eq 0 ]; then
       pubkey=$(age-keygen -y "$keyfile" | sed 's/^# public key: //')
     else
       pubkey=$(sudo age-keygen -y "$keyfile" | sed 's/^# public key: //')
     fi

      # Best-effort symlink for older setups that still expect the per-user path.
      if [ "$(uname -s)" = "Linux" ] && [ "$(id -u)" -eq 0 ] && [ -n "''${SUDO_USER:-}" ]; then
        userhome=$(getent passwd "$SUDO_USER" | cut -d: -f6 || true)
        if [ -n "$userhome" ]; then
          ensure_user_key_symlink "$userhome" || true
        fi
      elif [ "$(uname -s)" = "Linux" ]; then
        ensure_user_key_symlink "$HOME" || true
      fi

     echo "" >&2
     echo "Public AGE key:" >&2
     echo "$pubkey" >&2

     echo "" >&2
     echo "Add to pub/age-recipients.txt:" >&2
     echo "$pubkey  # $(hostname) (host)" >&2

     echo "" >&2
     echo "Add to .sops.yaml:" >&2
     echo "  - &server_$(hostname | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '_') $pubkey" >&2
     echo "  - *server_$(hostname | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '_')" >&2
  '';
}
