"""pengyos-update — fetch, verify and switch to a new PengyOS release.

    pengyos-update status              this machine: version, channel, slots
    pengyos-update check [--save]      is there a newer release on my channel?
    pengyos-update apply [--reboot]    install it into the other slot; next boot tries it
    pengyos-update confirm             (at boot) keep the slot that just came up
    pengyos-update rollback [--reboot] go back to the other slot
    pengyos-update channel [NAME]      show or set the channel (stable, testing)
    pengyos-update auto [on|off]       unattended updates in a nightly window (off by default)

An update never touches the running system or the data partition: it writes
the new image into the *other* root slot and asks GRUB to boot it once. If it
comes up and passes its health checks, ``confirm --boot``
(pengyos-update-confirm.service) makes it the default; if it doesn't, the next
boot is the old slot again, and that version is remembered as failed.

Before switching, ``apply`` runs the new image's own update steps
(``/usr/lib/pengyos/update.d/*`` inside the new system, as root): that is how a
release brings things that live outside the root slots, such as a new GRUB on
the boot partition. They come from the signed image, so they are as trusted as
the image itself.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

from . import __version__
from .release import (
    CHANNELS,
    UpdateError,
    load_keys,
    parse_feed,
    parse_manifest,
    parse_version,
    pick_update,
    sha256_file,
    verify_signed,
)
from .system import (
    CACHE,
    CONFIG,
    KEYS_DIR,
    Esp,
    detect_layout,
    device_size,
    kernel_args_from_grub_cfg,
    read_config,
    read_env,
    read_os_release,
    run,
    write_config_value,
)
from .state import MOTD, PROGRESS, STATE, Progress, load_state, now_iso, save_state, write_motd
from . import auto as autopol
from .trial import (
    HEALTH_DIRS,
    clear_trial,
    failure,
    failures_ahead,
    forget_failed,
    health_checks,
    mark_failed,
    record_trial,
    settle_trial,
    wait_healthy,
    write_confirmed,
)

_CHUNK = 4 << 20


def say(msg: str) -> None:
    print(msg, flush=True)


def require_root() -> None:
    if os.geteuid() != 0:
        raise UpdateError("this needs root — run it with sudo")


def lock() -> int:
    os.makedirs("/run/pengyos-update", exist_ok=True)
    fd = os.open("/run/pengyos-update/lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise UpdateError("another pengyos-update is already running") from None
    return fd


def fetch(url: str) -> bytes:
    r = subprocess.run(["curl", "-fsSL", "--retry", "3", "--connect-timeout", "20", url],
                       capture_output=True)
    if r.returncode != 0:
        raise UpdateError(f"could not download {url}: "
                          f"{r.stderr.decode(errors='replace').strip() or f'curl exit {r.returncode}'}")
    return r.stdout


def download(url: str, dest: Path, size: int, sha256: str, report=None) -> None:
    """Download (resuming) and check; a file already complete is kept.

    ``report(done_bytes)`` is called about twice a second while it runs."""
    if dest.exists() and dest.stat().st_size == size and sha256_file(dest) == sha256:
        return
    if dest.exists() and dest.stat().st_size >= size:
        dest.unlink()
    argv = ["curl", "-fL", "--retry", "3", "--connect-timeout", "20", "-C", "-", "-o", str(dest), url]
    argv.insert(1, "--progress-bar" if sys.stderr.isatty() else "-sS")
    proc = subprocess.Popen(argv)
    while proc.poll() is None:
        if report:
            try:
                report(dest.stat().st_size)
            except OSError:
                pass
        time.sleep(0.5)
    if proc.returncode != 0:
        raise UpdateError(f"downloading {url} failed (run again to resume)")
    if dest.stat().st_size != size or sha256_file(dest) != sha256:
        dest.unlink()
        raise UpdateError(f"{dest.name} does not match the signed manifest — deleted it; "
                          "run again to download it fresh")


def identity() -> tuple[str, str, dict[str, str]]:
    osr = read_os_release()
    image, version = osr.get("IMAGE_ID", ""), osr.get("IMAGE_VERSION", "")
    if not image or not version:
        raise UpdateError("/etc/os-release has no IMAGE_ID/IMAGE_VERSION — not an updatable PengyOS")
    return image, version, osr


def find_update(cfg: dict[str, str], image: str, current: str, channel: str,
                version: str | None = None):
    entries = parse_feed(fetch(cfg["feed"]))
    if version:
        parse_version(version)
        match = [e for e in entries if e.image == image and e.version == version]
        if not match:
            raise UpdateError(f"{image} {version} is not in the update feed")
        return match[0]
    return pick_update(entries, image, channel, current)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def slots_state() -> dict:
    """What the slots hold, for the saved state (root only; best effort)."""
    try:
        layout = detect_layout()
        env = read_env(layout.esp)
        other = other_slot_version(layout.slots[layout.other])
    except (UpdateError, OSError):
        return {}
    other_version = other.split()[1] if other.startswith("pengyos-") else None
    staged = other_version if env.get("pengyos_try") == layout.other else None
    return {"staged": staged, "other": other_version}


def login_message(state: dict, current: str) -> str | None:
    """The login message from what the state file says, or None for none.

    A version that failed here outranks "available" (installing it again would
    only fail again); an unattended run that went wrong is said too."""
    available = (state.get("available") or {}).get("version")
    lines = []
    for version, f in sorted((state.get("failed") or {}).items(),
                             key=lambda kv: parse_version(kv[0]), reverse=True):
        if parse_version(version) > parse_version(current) and isinstance(f, dict):
            lines.append(f"PengyOS {version} did not start on this machine ({f.get('at', '?')}: "
                         f"{f.get('reason', '?')}); still running {current}. "
                         "Details: pengyos-update status")
            break
    if available and not failure(state, available) and state.get("staged") != available:
        lines.append(f"PengyOS {available} is available (you have {current}): "
                     "sudo pengyos-update apply")
    elif state.get("staged"):
        lines.append(f"PengyOS {state['staged']} is installed — restart to start it.")
    last = state.get("auto") or {}
    if last.get("result") == "error":
        lines.append(f"The unattended update failed ({last.get('at', '?')}): {last.get('message', '?')}")
    return "\n".join(lines) or None


def cmd_check(args: argparse.Namespace) -> int:
    image, current, osr = identity()
    cfg = read_config()
    channel = args.channel or cfg["channel"]
    try:
        entries = parse_feed(fetch(cfg["feed"]))
        entry = pick_update(entries, image, channel, current)
    except UpdateError as exc:
        if not args.save:
            raise
        # Offline is normal for a laptop: record it, keep the last answer.
        save_state(args.state, error=str(exc), image=image, current=current, channel=channel)
        if not args.quiet:
            say(f"could not check: {exc}")
        return 0
    if args.save:
        fields = {"checked": now_iso(), "error": None, "image": image, "current": current,
                  "channel": channel, "name": osr.get("NAME", "PengyOS") + (
                      " " + osr["VARIANT"] if osr.get("VARIANT") else ""),
                  "available": ({"version": entry.version, "title": entry.title,
                                 "notes": entry.notes} if entry else None)}
        if os.geteuid() == 0:
            fields.update(slots_state())
        # The unattended run's soak window counts from the first sighting.
        fields["seen"] = autopol.record_seen(load_state(args.state).get("seen") or {}, entries,
                                             image, channel, dt.datetime.now(dt.timezone.utc))
        state = save_state(args.state, **fields)
        write_motd(login_message(state, current), args.motd)
    if args.json:
        print(json.dumps({"image": image, "current": current, "channel": channel,
                          "available": entry.version if entry else None}))
    elif args.quiet:
        pass
    elif entry:
        say(f"PengyOS {entry.version} is available ({channel}); you have {current}.")
        f = failure(load_state(args.state), entry.version)
        if f:
            say(f"It did not start on this machine before ({f.get('at', '?')}: {f.get('reason', '?')}).")
            say(f"Try it again with:  sudo pengyos-update apply --version {entry.version}")
        else:
            say("Install it with:  sudo pengyos-update apply")
    else:
        say(f"Up to date: {image} {current} is the newest on the {channel} channel.")
    return 0 if entry is None else 100 if args.exit_code else 0


def write_slot(src: Path, dev: str, raw_size: int, raw_sha256: str, progress: Progress | None = None) -> None:
    """``zstd -dc src`` onto ``dev``, hashing what is written."""
    if raw_size > device_size(dev):
        raise UpdateError(f"the new system ({raw_size >> 20} MiB) does not fit in {dev} — "
                          "this machine needs a reinstall to take it")
    with open("/proc/mounts") as fh:
        if any(line.split()[0] == dev for line in fh):
            raise UpdateError(f"{dev} is mounted — refusing to write over it")
    h, done, last = hashlib.sha256(), 0, -1
    zstd = subprocess.Popen(["zstd", "-dcq", str(src)], stdout=subprocess.PIPE)
    try:
        fd = os.open(dev, os.O_WRONLY)
        try:
            assert zstd.stdout is not None
            while chunk := zstd.stdout.read(_CHUNK):
                h.update(chunk)
                _write_all(fd, chunk)
                done += len(chunk)
                if done % (256 << 20) < len(chunk):
                    # Don't let gigabytes of written slot sit in the page cache
                    # (it pushes the desktop's memory out on small machines).
                    os.fdatasync(fd)
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                pct = done * 100 // max(raw_size, 1)
                if progress:
                    progress.update("write", done / max(raw_size, 1), "Writing the new system")
                if pct // 10 != last:
                    last = pct // 10
                    say(f"  writing the new system: {min(pct, 100)}%")
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        rc = zstd.wait()
    if rc != 0 or done != raw_size or h.hexdigest() != raw_sha256:
        raise UpdateError("the system image did not unpack to what the manifest signed — "
                          "the other slot is now unusable, but this one is untouched")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


HOOKS_DIR = "usr/lib/pengyos/update.d"
NEW_ROOT = "/run/pengyos-update/new"


def run_hooks(dev: str, env: dict[str, str]) -> None:
    """Run the NEW image's update steps (``/usr/lib/pengyos/update.d/*``) as root.

    They are part of the signed rootfs just written (its hash was checked while
    writing), mounted read-only here. A failing step stops the update before
    anything boots the new slot."""
    mnt = Path(NEW_ROOT)
    mnt.mkdir(parents=True, exist_ok=True)
    run(["mount", "-t", "ext4", "-o", "ro", dev, str(mnt)], "mounting the new system")
    try:
        hooks = sorted(p for p in (mnt / HOOKS_DIR).glob("*") if p.is_file() and os.access(p, os.X_OK))
        for hook in hooks:
            say(f"  update step: {hook.name}")
            full = {**os.environ, **env, "PENGYOS_NEW_ROOT": str(mnt)}
            r = subprocess.run([str(hook)], env=full, capture_output=True, text=True)
            for line in (r.stdout + r.stderr).strip().splitlines():
                say(f"    {line}")
            if r.returncode != 0:
                raise UpdateError(f"the new system's update step {hook.name} failed "
                                  f"(exit {r.returncode}) — not switching to it")
    finally:
        run(["umount", str(mnt)], "unmounting the new system", ok=(0, 1, 32))


def cmd_apply(args: argparse.Namespace) -> int:
    progress = Progress(args.progress)
    try:
        return _apply(args, progress)
    except UpdateError as exc:
        progress.update("error", 0.0, str(exc))
        raise
    except Exception as exc:  # noqa: BLE001 - the app must hear about it
        progress.update("error", 0.0, f"unexpected error: {exc}")
        raise


def _apply(args: argparse.Namespace, progress: Progress) -> int:
    require_root()
    _lock = lock()
    progress.update("start", 0.0, "Looking for the update")
    image, current, _ = identity()
    cfg = read_config()
    channel = args.channel or cfg["channel"]
    layout = detect_layout()
    env = read_env(layout.esp)
    committed = env.get("pengyos_slot", "a")
    if committed != layout.booted:
        raise UpdateError(f"this boot (slot {layout.booted}) is still on trial — slot {committed} "
                          "is the fallback. Run `sudo pengyos-update confirm` first to keep this one.")

    entry = find_update(cfg, image, current, channel, args.version)
    if entry is None:
        say(f"Up to date: {image} {current} is the newest on the {channel} channel.")
        progress.update("done", 1.0, "Already up to date", version=None)
        return 0
    f = failure(load_state(args.state), entry.version)
    if f and not args.version:
        raise UpdateError(f"PengyOS {entry.version} did not start on this machine before "
                          f"({f.get('at', '?')}: {f.get('reason', '?')}). To try it again: "
                          f"sudo pengyos-update apply --version {entry.version}")
    target = layout.other
    say(f"Updating {image} {current} -> {entry.version} (into slot {target}, {layout.slots[target]})")

    manifest_url = urljoin(cfg["feed"], entry.manifest_url)
    work = Path(CACHE) / image / entry.version
    work.mkdir(parents=True, exist_ok=True)
    data = fetch(manifest_url)
    sig = fetch(manifest_url + ".sig")
    keys = load_keys(args.keys)
    if not keys:
        raise UpdateError(f"no release keys in {args.keys} — cannot check anything")
    key = verify_signed(data, sig, keys, args.openssl)
    man = parse_manifest(data)
    say(f"  signed by key {key.keyid[:16]}… ({os.path.basename(key.path)}) — OK")
    if man.image != image:
        raise UpdateError(f"that release is for {man.image}, this machine runs {image}")
    if man.version != entry.version:
        raise UpdateError("the feed and the signed manifest disagree on the version — refusing")
    if parse_version(man.version) <= parse_version(current) and not args.version:
        raise UpdateError(f"{man.version} is not newer than {current} — refusing a downgrade")

    total = man.kernel.size + man.rootfs.size
    before = 0
    for ref in (man.kernel, man.rootfs):
        say(f"  downloading {ref.file} ({ref.size >> 20} MiB)")
        msg = f"Downloading PengyOS {man.version}"
        download(urljoin(manifest_url, ref.file), work / ref.file, ref.size, ref.sha256,
                 lambda n, b=before: progress.update("download", (b + n) / total, msg))
        before += ref.size
    say("  downloads match the signed manifest")
    progress.update("download", 1.0, "Checking the download")

    with Esp(layout.esp) as esp:
        # Nothing may boot the slot while it is half written.
        esp.set_env(pengyos_try="")
        write_slot(work / man.rootfs.file, layout.slots[target], man.rootfs.raw_size,
                   man.rootfs.raw_sha256, progress)
        dev = layout.slots[target]
        progress.update("finish", 0.1, "Finishing the new system")
        run(["e2fsck", "-fy", dev], "checking the new system", ok=(0, 1))
        run(["resize2fs", dev], "growing the new system to its slot")
        run(["tune2fs", "-U", "random", "-L", f"pengyos-root-{target}", dev],
            "labelling the new system")

        kdir = esp.path / f"slot-{target}"
        kdir.mkdir(exist_ok=True)
        tmp = kdir / "bzImage.new"
        shutil.copyfile(work / man.kernel.file, tmp)
        if sha256_file(tmp) != man.kernel.sha256:
            tmp.unlink()
            raise UpdateError("the kernel copy on the boot partition is damaged")
        os.replace(tmp, kdir / "bzImage")

        progress.update("finish", 0.5, "Running the new system's update steps")
        run_hooks(dev, {"PENGYOS_ESP": str(esp.path), "PENGYOS_ESP_DEV": layout.esp,
                        "PENGYOS_SLOT": target, "PENGYOS_SLOT_DEV": dev,
                        "PENGYOS_FROM": current, "PENGYOS_TO": man.version,
                        "PENGYOS_IMAGE": image})

        progress.update("finish", 0.8, "Setting up the boot menu")
        write_boot_menu(esp.boot_dir, layout.partuuids, keep_unparsed=False)
        esp.set_env(pengyos_slot=layout.booted, pengyos_try=target)

    shutil.rmtree(work, ignore_errors=True)
    state = load_state(args.state)
    avail = state.get("available") or {}
    save_state(args.state, staged=man.version, other=man.version,
               available=avail if avail.get("version") not in (None, man.version) else None)
    record_trial(args.state, man.version, current, target)
    forget_failed(args.state, man.version)  # asked for by name (--version): a new chance
    record_slots(layout, image, current, args.state)
    write_motd(f"PengyOS {man.version} is installed — restart to start it.", MOTD)
    progress.update("done", 1.0, f"PengyOS {man.version} is ready — restart to start it",
                    version=man.version)
    say(f"PengyOS {man.version} is installed in slot {target}. Restart to start it;")
    say(f"if it does not come up, the restart after that returns to {current}.")
    if args.reboot:
        say("Restarting now.")
        subprocess.run(["systemctl", "reboot"])
    return 0


def write_boot_menu(boot_dir: Path, partuuids: dict[str, str], keep_unparsed: bool) -> bool:
    """grub.cfg as THIS system's render_grub_cfg writes it, keeping the kernel
    arguments the current one has. Written (atomically) only if it differs.

    `apply` calls it on the old system, so a new slot first boots the old
    updater's template; `confirm` calls it on the new one, after it has
    started cleanly, so a boot-menu change lands on the update that ships it
    instead of the one after (the 26.10.52 console order reached nobody with
    26.10.52, 2026-10-31). With keep_unparsed, a grub.cfg whose kernel line
    cannot be read (edited by hand) is left alone instead of reset."""
    from pengyos_install.image import KERNEL_ARGS, render_grub_cfg
    cfg_path = boot_dir / "grub.cfg"
    old = cfg_path.read_text(encoding="utf-8") if cfg_path.is_file() else ""
    kargs = kernel_args_from_grub_cfg(old)
    if kargs is None and keep_unparsed:
        return False
    new = render_grub_cfg(partuuids, kargs or KERNEL_ARGS)
    if new == old:
        return False
    tmp = boot_dir / "grub.cfg.new"
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, cfg_path)
    return True


def health_timeout(cfg: dict[str, str]) -> int:
    try:
        return max(0, int(cfg.get("health_timeout", "180")))
    except ValueError:
        return 180


def cmd_confirm(args: argparse.Namespace) -> int:
    require_root()
    _lock = lock()
    layout = detect_layout()
    env = read_env(layout.esp)
    committed = env.get("pengyos_slot", "a")
    already = committed == layout.booted and not env.get("pengyos_try")
    image, version, _ = identity()
    if already:
        write_confirmed()
        if args.boot:
            # A recorded trial that is not this slot, with pengyos_try consumed,
            # booted and never kept itself (panic, hang, emergency mode, failed
            # checks): remember it, so nothing installs it again by itself.
            bad = settle_trial(args.state, layout.booted, committed, env.get("pengyos_try", ""))
            if bad:
                say(f"PengyOS {bad} was tried and did not start; this is {version} again")
    elif args.boot and committed != layout.booted:
        # This boot is a trial: keep it only if it is healthy.
        checks = health_checks(tuple(args.health_dirs) if args.health_dirs else HEALTH_DIRS)
        say(f"trial boot of {version}: {len(checks)} health check(s) "
            f"({', '.join(c.name for c in checks) or 'none'})")
        failures = wait_healthy(checks, health_timeout(read_config()), say=say)
        if failures:
            reason = "health check failed: " + "; ".join(failures)
            mark_failed(args.state, version, reason)
            say(f"not keeping {version}: {reason}")
            say(f"restarting into the previous system (slot {committed})")
            if args.reboot_cmd:
                subprocess.run(shlex.split(args.reboot_cmd))
            return 1
        say("healthy")
    with Esp(layout.esp) as esp:
        if not already:
            esp.set_env(pengyos_slot=layout.booted, pengyos_try="")
        if write_boot_menu(esp.boot_dir, layout.partuuids, keep_unparsed=True):
            say("boot menu rewritten by this system's updater")
    record_slots(layout, image, version, args.state)
    if already:
        write_motd(login_message(load_state(args.state), version), args.motd)
        say(f"slot {layout.booted} is already the default")
        return 0
    write_confirmed()
    clear_trial(args.state)
    forget_failed(args.state, version)  # kept by hand, or healthy after all
    state = load_state(args.state)
    if state:
        avail = state.get("available") or {}
        state = save_state(args.state, staged=None, current=version,
                           available=avail if avail.get("version") != version else None)
    write_motd(login_message(state, version), args.motd)
    say(f"started cleanly: slot {layout.booted} ({version}) is now the default")
    return 0


def other_slot_version(dev: str) -> str:
    mnt = Path("/run/pengyos-update/other")
    mnt.mkdir(parents=True, exist_ok=True)
    try:
        run(["mount", "-t", "ext4", "-o", "ro,noload", dev, str(mnt)], "mounting the other slot")
    except UpdateError:
        return "(empty)"
    try:
        osr = read_os_release(str(mnt / "usr/lib/os-release"))
        return f"{osr.get('IMAGE_ID', '?')} {osr.get('IMAGE_VERSION', '?')}"
    except OSError:
        return "(not a PengyOS system)"
    finally:
        run(["umount", str(mnt)], "unmounting the other slot", ok=(0, 1, 32))


def cmd_rollback(args: argparse.Namespace) -> int:
    require_root()
    _lock = lock()
    layout = detect_layout()
    other = layout.other
    what = other_slot_version(layout.slots[other])
    if not what.startswith("pengyos-"):
        raise UpdateError(f"slot {other} holds no PengyOS system {what} — nothing to go back to")
    with Esp(layout.esp) as esp:
        if not (esp.path / f"slot-{other}" / "bzImage").is_file():
            raise UpdateError(f"slot {other} has no kernel on the boot partition")
        if f"--id pengyos-{other}" not in (esp.boot_dir / "grub.cfg").read_text(encoding="utf-8"):
            raise UpdateError(f"the boot menu has no entry for slot {other}")
        esp.set_env(pengyos_slot=other, pengyos_try="")
    if load_state():
        save_state(staged=what.split()[1])
    clear_trial(STATE)
    image, current, _ = identity()
    record_slots(layout, image, current)
    say(f"slot {other} ({what}) is the default from the next restart.")
    if args.reboot:
        subprocess.run(["systemctl", "reboot"])
    return 0


def slot_rows(layout, env: dict, image: str, current: str) -> list[dict]:
    """The A/B table: per slot its partition, system and tags (root: mounts the other slot)."""
    committed = env.get("pengyos_slot", "a")
    rows = []
    for s in ("a", "b"):
        tags = []
        if s == layout.booted:
            tags.append("running")
        if s == committed:
            tags.append("default")
        if env.get("pengyos_try") == s:
            tags.append("tried once at next boot")
        ver = f"{image} {current}" if s == layout.booted else other_slot_version(layout.slots[s])
        rows.append({"slot": s, "device": layout.slots[s], "system": ver, "tags": tags})
    return rows


def print_slots(rows: list[dict]) -> None:
    for r in rows:
        tags = r.get("tags") or []
        say(f"slot {r['slot']}:   {r['device']}  {r['system']}  {'[' + ', '.join(tags) + ']' if tags else ''}")


def record_slots(layout, image: str, current: str, state_path: str = STATE) -> None:
    """Keep the slot table in the world-readable state file, so `status` needs no sudo.

    Called by the commands that already run as root and change or prove the table:
    confirm (every boot), apply and rollback. Never fails the caller.
    """
    try:
        rows = slot_rows(layout, read_env(layout.esp), image, current)
        save_state(state_path, slots=rows, slots_at=now_iso())
    except (UpdateError, OSError) as exc:
        say(f"(could not record the slots for `pengyos-update status`: {exc})")


def _age_s(iso: str) -> int | None:
    """Seconds since an ISO-8601 UTC stamp (now_iso's form), or None if unreadable."""
    try:
        t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return max(0, int((dt.datetime.now(dt.timezone.utc) - t).total_seconds()))


def _human(seconds: int) -> str:
    for unit, n in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= n:
            k = seconds // n
            return f"{k} {unit}{'s' if k != 1 else ''}"
    return "less than a minute"


def cmd_status(args: argparse.Namespace) -> int:
    image, current, osr = identity()
    cfg = read_config()
    saved = load_state(args.state)
    out = {"image": image, "current": current, "build": osr.get("BUILD_ID", ""),
           "channel": cfg["channel"], "feed": cfg["feed"],
           "auto": {**autopol.settings(cfg), "last": saved.get("auto")},
           "failed": saved.get("failed") or {}, "trial": saved.get("trial"),
           "slots": [], "slots_source": "none", "slots_at": None, "slots_age_s": None}
    if os.geteuid() == 0:
        layout = detect_layout()
        rows = slot_rows(layout, read_env(layout.esp), image, current)
        at = now_iso()
        save_state(args.state, slots=rows, slots_at=at)
        out.update(slots=rows, slots_source="live", slots_at=at, slots_age_s=0)
    else:
        # Without root: the table root last recorded, with "running" and this slot's
        # system read live, so a boot that never reached confirm is still shown as it
        # is. The other slot's system is only readable as root (it is a block device),
        # but nothing changes it without recording: confirm (every boot), apply and
        # rollback all rewrite this table.
        state = load_state(args.state)
        rows = state.get("slots") or []
        if rows:
            try:
                booted = detect_layout().booted
            except UpdateError:
                booted = None
            for r in rows:
                tags = [t for t in r.get("tags") or [] if t != "running"]
                if r.get("slot") == booted:
                    tags.insert(0, "running")
                    r["system"] = f"{image} {current}"
                r["tags"] = tags
            at = state.get("slots_at")
            out.update(slots=rows, slots_source="recorded", slots_at=at, slots_age_s=_age_s(at))
    if getattr(args, "json", False):
        print(json.dumps(out, indent=2))
        return 0
    say(f"system:   {osr.get('PRETTY_NAME', image)} ({image} {current}, build {out['build'] or '?'})")
    say(f"channel:  {cfg['channel']}   feed: {cfg['feed']}")
    pol = out["auto"]
    say("unattended: " + (f"on, daily at {pol['at']}, soak {pol['soak_days']} day(s)"
                          if pol["enabled"] else "off (pengyos-update auto on)"))
    if pol["last"]:
        last = pol["last"]
        say(f"  last run {last.get('at', '?')}: {last.get('result', '?')}"
            + (f" — {last['message']}" if last.get("message") else ""))
    for version, f in failures_ahead(saved, current):
        say(f"failed:   {version} did not start here ({f.get('at', '?')}: {f.get('reason', '?')})")
    if not out["slots"]:
        say("(run with sudo to see the slots)")
        return 0
    print_slots(out["slots"])
    if out["slots_source"] == "recorded":
        age = out["slots_age_s"]
        when = f"{out['slots_at'] or '?'}" + (f", {_human(age)} ago" if age is not None else "")
        say(f"(slots as recorded {when}, at the last boot, apply or rollback, which are what "
            "change them; sudo reads them live)")
    return 0

def cmd_channel(args: argparse.Namespace) -> int:
    cfg = read_config()
    if not args.name:
        say(cfg["channel"])
        return 0
    if args.name not in CHANNELS:
        raise UpdateError(f"unknown channel {args.name!r} (use one of: {', '.join(CHANNELS)})")
    require_root()
    write_config_value("channel", args.name)
    say(f"channel set to {args.name}")
    return 0


def systemctl(*argv: str, ok: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    return run(["systemctl", *argv], f"systemctl {' '.join(argv)}", ok=ok)


def cmd_auto(args: argparse.Namespace) -> int:
    cfg = read_config(args.config)
    pol = autopol.settings(cfg)
    if args.mode is None:
        state = load_state(args.state)
        say(f"unattended updates: {'on' if pol['enabled'] else 'off'}")
        if pol["enabled"]:
            say(f"  every day at {pol['at']}, releases first seen {pol['soak_days']} day(s) ago or "
                f"more, from the {cfg['channel']} channel; restarts only after a good install")
        last = state.get("auto")
        if last:
            say(f"  last run {last.get('at', '?')}: {last.get('result', '?')}"
                + (f" — {last['message']}" if last.get("message") else ""))
        try:
            shown = failures_ahead(state, identity()[1])
        except UpdateError:  # not a PengyOS system: show them all
            shown = sorted((state.get("failed") or {}).items())
        for version, f in shown:
            say(f"  {version} failed here ({f.get('at', '?')}: {f.get('reason', '?')}); "
                "it is not installed again unattended")
        if not pol["enabled"]:
            say("turn on with:  sudo pengyos-update auto on [--at HH:MM] [--soak DAYS]")
        return 0
    require_root()
    if args.mode == "off":
        write_config_value("auto", "off", args.config)
        systemctl("disable", "--now", autopol.TIMER, ok=(0, 1))
        Path(args.dropin).unlink(missing_ok=True)
        systemctl("daemon-reload", ok=(0, 1))
        say("unattended updates are off; `pengyos-update check` still looks daily")
        return 0
    at = autopol.parse_at(args.at) if args.at else pol["at"]
    soak = autopol.parse_soak(args.soak) if args.soak is not None else pol["soak_days"]
    write_config_value("auto", "on", args.config)
    write_config_value("auto_at", at, args.config)
    write_config_value("auto_soak_days", str(soak), args.config)
    autopol.write_timer_dropin(at, args.dropin)
    systemctl("daemon-reload")
    systemctl("enable", "--now", autopol.TIMER)
    say(f"unattended updates are on: every day at {at}, {cfg['channel']} releases at least "
        f"{soak} day(s) after this machine first saw them. It restarts only after a "
        "good install, and a release that does not come up is undone and not retried.")
    if cfg["channel"] != "stable":
        say(f"warning: this machine is on the {cfg['channel']} channel — expect a restart most "
            "nights. `sudo pengyos-update channel stable` for tested releases only.")
    return 0


def _auto_record(path: str, result: str, message: str = "", version: str | None = None) -> None:
    state = save_state(path, auto={"at": now_iso(), "result": result, "message": message,
                                   "version": version})
    try:
        _, current, _ = identity()
        write_motd(login_message(state, current), MOTD)
    except UpdateError:
        pass
    say(f"{result}: {message}" if message else result)


def cmd_auto_run(args: argparse.Namespace) -> int:
    require_root()
    cfg = read_config(args.config)
    pol = autopol.settings(cfg)
    if not pol["enabled"]:
        say("unattended updates are off (pengyos-update auto on)")
        return 0
    try:
        image, current, _ = identity()
        layout = detect_layout()
        env = read_env(layout.esp)
        if env.get("pengyos_slot", "a") != layout.booted:
            _auto_record(args.state, "skipped", "this boot is a trial that is not confirmed yet")
            return 0
        if env.get("pengyos_try"):
            _auto_record(args.state, "skipped", "an update is already installed and waits for a "
                                                "restart (sudo systemctl reboot)")
            return 0
        channel = cfg["channel"]
        entries = parse_feed(fetch(cfg["feed"]))
        now = dt.datetime.now(dt.timezone.utc)
        state = load_state(args.state)
        seen = autopol.record_seen(state.get("seen") or {}, entries, image, channel, now)
        state = save_state(args.state, seen=seen)
        entry, waiting, until = autopol.pick(entries, image, channel, current, state, seen,
                                             pol["soak_days"], now)
        if entry is None:
            if waiting:
                _auto_record(args.state, "waiting", f"{waiting.version} is installed from {until} "
                             f"(soak {pol['soak_days']} days)", waiting.version)
            else:
                _auto_record(args.state, "nothing-newer", f"{current} is the newest to install")
            return 0
        say(f"unattended update: {current} -> {entry.version}")
        apply_args = argparse.Namespace(channel=None, version=entry.version, reboot=False,
                                        keys=args.keys, openssl=args.openssl, state=args.state)
        _apply(apply_args, Progress(None))
    except UpdateError as exc:
        _auto_record(args.state, "error", str(exc))
        return 1
    _auto_record(args.state, "applied", f"{entry.version} installed; restarting into it",
                 entry.version)
    if args.reboot_cmd:
        subprocess.run(shlex.split(args.reboot_cmd))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pengyos-update", description=__doc__.split("\n\n")[0])
    p.add_argument("--version", action="version", version=f"pengyos-update {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="this machine: version, channel, slots")
    s.add_argument("--state", default=STATE, help=argparse.SUPPRESS)
    s.add_argument("--json", action="store_true",
                   help="machine-readable, with where the slot table came from and its age")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("check", help="is there a newer release?")
    s.add_argument("--channel", choices=sorted(CHANNELS))
    s.add_argument("--json", action="store_true")
    s.add_argument("--exit-code", action="store_true", help="exit 100 when an update is available")
    s.add_argument("--save", action="store_true",
                   help=f"record the answer for the desktop app and the login message ({STATE})")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--state", default=STATE, help=argparse.SUPPRESS)
    s.add_argument("--motd", default=MOTD, help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("apply", help="install the newest release into the other slot")
    s.add_argument("--channel", choices=sorted(CHANNELS))
    s.add_argument("--version", dest="version", metavar="YY.MM.N",
                   help="install exactly this release (also allows going back to an older one)")
    s.add_argument("--reboot", action="store_true", help="restart into it when done")
    s.add_argument("--keys", default=KEYS_DIR, help=argparse.SUPPRESS)
    s.add_argument("--openssl", default="openssl", help=argparse.SUPPRESS)
    s.add_argument("--progress", metavar="FILE", nargs="?", const=PROGRESS,
                   help=f"write progress as JSON to FILE (default {PROGRESS})")
    s.add_argument("--state", default=STATE, help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("confirm", help="keep the slot that is running (run at boot)")
    s.add_argument("--boot", action="store_true",
                   help="as the boot does it: keep a trial only if its health checks pass "
                        "(else restart into the previous system), and record trials that failed")
    s.add_argument("--state", default=STATE, help=argparse.SUPPRESS)
    s.add_argument("--motd", default=MOTD, help=argparse.SUPPRESS)
    s.add_argument("--health-dir", dest="health_dirs", action="append", help=argparse.SUPPRESS)
    s.add_argument("--reboot-cmd", default="systemctl --no-block reboot", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_confirm)

    s = sub.add_parser("rollback", help="make the other slot the default again")
    s.add_argument("--reboot", action="store_true")
    s.set_defaults(fn=cmd_rollback)

    s = sub.add_parser("channel", help=f"show or set the channel ({CONFIG})")
    s.add_argument("name", nargs="?")
    s.set_defaults(fn=cmd_channel)

    s = sub.add_parser("auto", help="show or set unattended updates (off by default)")
    s.add_argument("mode", nargs="?", choices=("on", "off"))
    s.add_argument("--at", metavar="HH:MM", help=f"daily time (default {autopol.DEFAULT_AT})")
    s.add_argument("--soak", metavar="DAYS",
                   help="wait this long after a release first appears "
                        f"(default {autopol.DEFAULT_SOAK_DAYS})")
    s.add_argument("--config", default=CONFIG, help=argparse.SUPPRESS)
    s.add_argument("--dropin", default=autopol.TIMER_DROPIN, help=argparse.SUPPRESS)
    s.add_argument("--state", default=STATE, help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_auto)

    s = sub.add_parser("auto-run", help="(pengyos-update-auto.timer) install and restart, if due")
    s.add_argument("--config", default=CONFIG, help=argparse.SUPPRESS)
    s.add_argument("--state", default=STATE, help=argparse.SUPPRESS)
    s.add_argument("--keys", default=KEYS_DIR, help=argparse.SUPPRESS)
    s.add_argument("--openssl", default="openssl", help=argparse.SUPPRESS)
    s.add_argument("--reboot-cmd", default="systemctl --no-block reboot", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_auto_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except UpdateError as exc:
        print(f"pengyos-update: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("pengyos-update: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
