"""Platform-owned seal/retire step for AXIS acceptance jobs. Runs as root via systemd only.

R8 remediation (Chris HOLD on ee10310a):
- Complete+validate published tree in private staging jobs/<id>.publishing, then
  atomic rename into jobs/<id>. Incomplete/.publishing/.sealing never authorize.
- Bind published bytes: SEALED records candidate digest; after copy, re-digest the
  staged publish tree and refuse mismatch before the atomic rename. Entry also
  re-verifies published snapshot vs SEALED before exec (see platform module).
- Reject hard links and symlinks.
- Test hooks: AXIS_SEAL_TEST_BARRIER (after lock, before digest) and
  AXIS_SEAL_TEST_PUBLISH_BARRIER (after staging copy, before verify/rename).
- Retire clears published, .sealing, and .publishing trees.
"""
import hashlib, json, os, pathlib, re, shutil, sys, time

SPOOL = pathlib.Path("/var/lib/axis-acceptance")
VERSION = "axis.tree-manifest.v1"


def tree_digest(root: pathlib.Path) -> str:
    h = hashlib.sha256((VERSION + "\n").encode())
    for p in sorted(root.rglob("*"), key=lambda q: q.relative_to(root).as_posix()):
        rel = p.relative_to(root).as_posix()
        st = p.lstat()
        if p.is_symlink():
            e = {"path": rel, "type": "symlink", "target": os.readlink(p)}
        elif p.is_dir():
            e = {"path": rel, "type": "dir", "mode": st.st_mode & 0o7777}
        elif p.is_file():
            e = {"path": rel, "type": "file", "mode": st.st_mode & 0o7777, "size": st.st_size,
                 "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        else:
            raise SystemExit(f"unsupported entry type: {rel}")
        blob = json.dumps(e, sort_keys=True, separators=(",", ":")).encode()
        h.update(len(blob).to_bytes(8, "big") + blob)
    return h.hexdigest()


def _validate_tree(root: pathlib.Path) -> None:
    for p in [root, *root.rglob("*")]:
        st = p.lstat()
        if p.is_symlink():
            raise SystemExit(f"symlink rejected: {p.relative_to(root)}")
        if p.is_file() and st.st_nlink > 1:
            raise SystemExit(f"hardlink rejected: {p.relative_to(root)} nlink={st.st_nlink}")


def _lock_tree(root: pathlib.Path) -> None:
    for p in [root, *sorted(root.rglob("*"), key=lambda q: len(q.parts))]:
        if p.is_symlink():
            continue
        os.chown(p, 0, 0)
        mode = 0o555 if p.is_dir() else (p.stat().st_mode & 0o555)
        os.chmod(p, mode)


def _wait_barrier(barrier: str) -> None:
    pathlib.Path(barrier + ".ready").write_text("ready\n")
    while not pathlib.Path(barrier + ".cont").exists():
        time.sleep(0.05)


def _publish_atomic(partial: pathlib.Path, dst: pathlib.Path, expected_digest: str) -> None:
    """Copy into non-executable staging, bind-verify digest, atomic rename into jobs/<id>."""
    if dst.exists():
        raise SystemExit("publish destination exists")
    staging = dst.parent / f"{dst.name}.publishing"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        shutil.copytree(partial, staging, symlinks=False)
        _lock_tree(staging)
        publish_barrier = os.environ.get("AXIS_SEAL_TEST_PUBLISH_BARRIER")
        if publish_barrier:
            _wait_barrier(publish_barrier)
        # Optional controlled divergence after candidate digest / before rename.
        mutate = os.environ.get("AXIS_SEAL_TEST_MUTATE_PUBLISH")
        if mutate:
            target = staging / "snapshot" / "src" / "probe_mod.py"
            os.chmod(target, 0o644)
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(mutate)
            os.chmod(target, 0o444)
        got = tree_digest(staging / "snapshot")
        if got != expected_digest:
            raise SystemExit(f"published digest mismatch: {got} != {expected_digest}")
        sealed_text = (staging / "SEALED").read_text().strip()
        if sealed_text != expected_digest:
            raise SystemExit(f"SEALED content mismatch: {sealed_text} != {expected_digest}")
        # Drop partial before rename so jobs/<id> never coexists with .sealing.
        shutil.rmtree(partial)
        # Atomic publication: same-filesystem rename; jobs/<id> appears only complete.
        os.rename(staging, dst)
    except BaseException:
        # Include SystemExit so mismatch/failure never leaves a half-published staging tree.
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    action, job_id = sys.argv[1], sys.argv[2]
    if not re.fullmatch(r"[a-z0-9-]{8,64}", job_id):
        return 64
    src = SPOOL / "incoming" / job_id
    dst = SPOOL / "jobs" / job_id
    partial = SPOOL / "jobs" / f"{job_id}.sealing"
    staging = SPOOL / "jobs" / f"{job_id}.publishing"
    if action == "retire":
        shutil.rmtree(dst, ignore_errors=True)
        shutil.rmtree(partial, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        return 0
    if action != "seal":
        return 65
    if dst.exists() or partial.exists() or staging.exists() or not src.is_dir():
        return 65
    os.rename(src, partial)
    # Lock immediately after rename so axis cannot mutate during the rest of seal.
    _lock_tree(partial)
    barrier = os.environ.get("AXIS_SEAL_TEST_BARRIER")
    if barrier:
        _wait_barrier(barrier)
    try:
        _validate_tree(partial)
        (partial / "result").mkdir(mode=0o700)
        expected = tree_digest(partial / "snapshot")
        (partial / "SEALED").write_text(expected + "\n")
        os.chmod(partial / "SEALED", 0o444)
        # Break FD aliases via new inodes; bind+atomic publish into jobs/<id>.
        _publish_atomic(partial, dst, expected)
    except SystemExit:
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
