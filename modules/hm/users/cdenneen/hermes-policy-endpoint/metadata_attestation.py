"""
Host-local peer-process attestation for x_hermes_source (#40).

Problem: x_hermes_source arrives in the HTTP request body, which makes it
ASSERTED, not ATTESTED - any local process able to reach 127.0.0.1:860x
can currently send an arbitrary value. It is provably floor-safe today
(most_restrictive() can never let it widen past the gateway/tool ceiling
- see action_classification.py), so this is not an active vulnerability,
but per instruction it should not be treated as more trustworthy than it
is, and the mechanism below is the smallest robust hardening available
without cryptography or a transport change.

Mechanism: kernel-guaranteed peer-process identity over the *existing*
TCP loopback socket, via /proc - the same technique `ss -p`/`lsof -i`
use, no cryptography, no change to Hermes/the OpenAI SDK's HTTP client:

1. The workload-metadata sitecustomize patch (piece 2) already knows the
   session source at turn entry. It additionally writes it, keyed by its
   OWN pid, to a small per-instance side-channel directory
   (mode 0700/0600, same local user only).
2. On each request, this module resolves the TCP peer's pid by scanning
   /proc/net/tcp for the connection's inode, then /proc/*/fd/* for which
   process owns that socket inode - the same non-spoofable mechanism
   `ss -p` relies on: a different local process cannot fabricate another
   PID's open file descriptors.
3. If the peer pid is resolved AND that pid's side-channel file agrees
   with the request body's x_hermes_source, provenance upgrades from
   ASSERTED to ATTESTED. If they disagree, that is a #39C
   ``metadata_conflict`` - the more restrictive of the two wins, never
   the more permissive one. If the peer pid can't be resolved (non-Linux,
   permission denied, timing race, the file doesn't exist), this fails
   closed to today's status quo: x_hermes_source stays ASSERTED-only,
   still floor-safe by construction - never fails open to "trust it
   unconditionally."

This intentionally does NOT attempt to verify the peer pid's *identity*
(e.g. "is this really the hermes binary") - PID/fd-inode ownership from
/proc is already kernel-enforced and unspoofable by a different local
process running as the same user; that is the boundary this hardens,
consistent with the instruction to prefer a host-local boundary over
cryptographic signing where one is sufficient.
"""

import json
import os
import time
from pathlib import Path

_LOOPBACK_HEX = "0100007F"  # /proc/net/tcp's representation of 127.0.0.1
_MAX_SIDE_CHANNEL_AGE_S = 30.0  # a turn-entry write must be recent to count

# Shared, instance-independent location - deliberately NOT nested under any
# one hermes-policy-endpoint instance's own state root. A peer pid belongs
# to exactly one running Hermes process regardless of which policy-endpoint
# instance happens to be checking it, so one shared directory (keyed by
# pid) is correct rather than duplicating this per instance. Hermes's own
# sitecustomize.py writes here directly (inlined, self-contained, matching
# the existing precedent of not importing repo-internal modules into
# Hermes's process) - this constant must match that inlined path exactly.
SHARED_PEER_SOURCE_DIR = Path.home() / ".hermes-policy" / "_peer-source"


def side_channel_dir() -> Path:
    SHARED_PEER_SOURCE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    return SHARED_PEER_SOURCE_DIR


def write_side_channel(source) -> None:
    """Called by the sitecustomize patch's own process - writes its
    current turn's source keyed by its own pid. Never raises; a failure
    here just means this request won't get attestation upgrade."""
    try:
        d = side_channel_dir()
        path = d / f"{os.getpid()}.json"
        path.write_text(json.dumps({"source": source, "ts": time.time()}))
        path.chmod(0o600)
    except OSError:
        pass


def _find_inode_for_connection(local_port: int, peer_port: int) -> int | None:
    """The /proc/net/tcp line describing the PEER's own socket is the one
    where, from the peer's perspective, its local port is our peer_port
    and its remote port is our local_port - the mirror image of our own
    accept()ed connection's line."""
    local_hex = f"{local_port:04X}"
    peer_hex = f"{peer_port:04X}"
    try:
        with open("/proc/net/tcp") as f:
            next(f)  # header
            for line in f:
                fields = line.split()
                if len(fields) < 10:
                    continue
                local_addr, remote_addr = fields[1], fields[2]
                if (
                    local_addr == f"{_LOOPBACK_HEX}:{peer_hex}"
                    and remote_addr == f"{_LOOPBACK_HEX}:{local_hex}"
                ):
                    return int(fields[9])
    except OSError:
        return None
    return None


def _find_pid_for_inode(inode: int) -> int | None:
    target = f"socket:[{inode}]"
    try:
        for pid_str in os.listdir("/proc"):
            if not pid_str.isdigit():
                continue
            fd_dir = f"/proc/{pid_str}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        link = os.readlink(f"{fd_dir}/{fd}")
                    except OSError:
                        continue
                    if link == target:
                        return int(pid_str)
            except OSError:
                continue
    except OSError:
        return None
    return None


def resolve_peer_pid(local_port: int, peer_port: int) -> int | None:
    """Best-effort, Linux-only, never raises. Returns None on any
    failure - callers must treat that as "attestation unavailable," not
    as a denial signal by itself."""
    try:
        inode = _find_inode_for_connection(local_port, peer_port)
        if inode is None:
            return None
        return _find_pid_for_inode(inode)
    except Exception:
        return None


def attested_source_for_peer(local_port: int, peer_port: int):
    """Returns (attested_source_or_None, attempted: bool). attempted=False
    means resolution itself failed (non-Linux, permission, timing) -
    distinct from attempted=True/value=None, which means resolution
    succeeded but no side-channel file exists for that pid (e.g. the peer
    isn't the patched Hermes process at all)."""
    pid = resolve_peer_pid(local_port, peer_port)
    if pid is None:
        return None, False
    path = side_channel_dir() / f"{pid}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, True
    if time.time() - data.get("ts", 0) > _MAX_SIDE_CHANNEL_AGE_S:
        return None, True
    return data.get("source"), True
