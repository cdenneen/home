"""Platform-owned seal/retire step for AXIS acceptance jobs. Runs as root via systemd only.

R8 workdir extension (approved package baseline unchanged):
- atomic publish via jobs/<id>.sealing -> copy-to-jobs/<id> (new inodes) so FDs
  held across seal cannot mutate published bytes
- reject hard links and symlinks
- optional AXIS_SEAL_TEST_BARRIER for controlled concurrent-mutation ordering
- retire clears published and partial trees
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


def _publish_new_inodes(partial: pathlib.Path, dst: pathlib.Path) -> None:
    """Copy to destination with fresh inodes so pre-seal writable FDs cannot mutate publish."""
    if dst.exists():
        raise SystemExit("publish destination exists")
    shutil.copytree(partial, dst, symlinks=False)
    _lock_tree(dst)
    shutil.rmtree(partial)


def main() -> int:
    action, job_id = sys.argv[1], sys.argv[2]
    if not re.fullmatch(r"[a-z0-9-]{8,64}", job_id):
        return 64
    src = SPOOL / "incoming" / job_id
    dst = SPOOL / "jobs" / job_id
    partial = SPOOL / "jobs" / f"{job_id}.sealing"
    if action == "retire":
        shutil.rmtree(dst, ignore_errors=True)
        shutil.rmtree(partial, ignore_errors=True)
        return 0
    if action != "seal":
        return 65
    if dst.exists() or partial.exists() or not src.is_dir():
        return 65
    os.rename(src, partial)
    # Lock immediately after rename so axis cannot mutate during the rest of seal.
    _lock_tree(partial)
    barrier = os.environ.get("AXIS_SEAL_TEST_BARRIER")
    if barrier:
        pathlib.Path(barrier + ".ready").write_text("ready\n")
        while not pathlib.Path(barrier + ".cont").exists():
            time.sleep(0.05)
    try:
        _validate_tree(partial)
        (partial / "result").mkdir(mode=0o700)
        (partial / "SEALED").write_text(tree_digest(partial / "snapshot") + "\n")
        os.chmod(partial / "SEALED", 0o444)
        # Break FD aliases: published tree must be new inodes.
        _publish_new_inodes(partial, dst)
    except SystemExit:
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
