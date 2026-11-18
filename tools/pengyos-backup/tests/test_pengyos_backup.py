"""Tests for the `pengyos-backup` shipped tool, and the image wiring around it.

The shipped script runs as a program against a sandbox root (`--root DIR`) with
fakes on PATH for restic, btrfs, unshare, mount, systemctl, ssh-keygen and
pengyos-alert. Each fake records its argv (and restic its environment) as JSON
lines, so what the tool would run on a machine is what is asserted. When a real
`restic` is on PATH, one test also backs up and restores through it for real.

That the snapshots, the private mount namespace and an sftp: repository work on a
PengyOS machine is proven on hardware (spec.md, "Acceptance").
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_DIR.parents[1]
OVERLAY = REPO_ROOT / "buildroot-external" / "board" / "server" / "rootfs-overlay"
SCRIPT = OVERLAY / "usr" / "bin" / "pengyos-backup"

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
name = os.path.basename(sys.argv[0])
log = os.environ["FAKE_LOG"]
rec = {"tool": name, "argv": sys.argv[1:]}
if name == "restic":
    rec["env"] = {k: v for k, v in os.environ.items()
                  if k.startswith("RESTIC_") or k in ("AWS_ACCESS_KEY_ID",)}
with open(log, "a") as f:
    f.write(json.dumps(rec) + "\n")
fail = os.environ.get("FAKE_FAIL", "")
if name == "btrfs" and sys.argv[1:3] == ["inspect-internal", "rootid"]:
    ids = json.loads(os.environ.get("FAKE_ROOTIDS", "{}"))
    p = sys.argv[3]
    while p not in ids and p not in ("/", ""):
        p = os.path.dirname(p)
    if p not in ids:
        sys.exit(1)
    print(ids[p]); sys.exit(0)
if name == "btrfs" and sys.argv[1:3] == ["subvolume", "snapshot"]:
    os.makedirs(sys.argv[-1], exist_ok=True); sys.exit(0)
if name == "btrfs" and sys.argv[1:3] == ["subvolume", "delete"]:
    os.rmdir(sys.argv[-1]); sys.exit(0)
if name == "unshare":
    i = sys.argv.index("--")
    os.execvp(sys.argv[i + 1], sys.argv[i + 1:])
if name == "ssh-keygen":
    k = sys.argv[sys.argv.index("-f") + 1]
    open(k, "w").write("PRIVATE"); open(k + ".pub", "w").write("ssh-ed25519 AAAAfake pengyos-backup@x")
    sys.exit(0)
if name == "restic":
    args = [a for a in sys.argv[1:]]
    while args and args[0] == "-o":
        args = args[2:]
    cmd = args[0] if args else ""
    if cmd in fail.split(","):
        print(f"Fatal: fake {cmd} failure", file=sys.stderr); sys.exit(1)
    if cmd == "cat":
        sys.exit(0 if os.environ.get("FAKE_REPO_EXISTS") else 1)
    if cmd == "backup":
        print(json.dumps({"message_type": "status", "percent_done": 0.5}))
        print(json.dumps({"message_type": "summary", "snapshot_id": "abcdef1234567890",
                          "files_new": 3, "data_added": 4096, "total_files_processed": 10}))
    if cmd == "snapshots":
        print(json.dumps([{"short_id": "abcdef12", "time": "2026-11-17T03:00:00Z",
                           "tags": ["job:home"], "paths": ["/home"]}]))
sys.exit(0)
'''


@pytest.fixture()
def env(tmp_path):
    root = tmp_path / "root"
    shutil.copytree(OVERLAY / "etc" / "pengyos", root / "etc" / "pengyos",
                    ignore=shutil.ignore_patterns("alert*"))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "fake"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    for tool in ("restic", "btrfs", "unshare", "mount", "systemctl", "ssh-keygen", "pengyos-alert"):
        (bindir / tool).symlink_to(fake)
    log = tmp_path / "calls.jsonl"
    data = tmp_path / "data"  # stands in for the machine's paths named by jobs
    (data / "home" / "me").mkdir(parents=True)
    (data / "home" / "me" / "file.txt").write_text("hello")
    (data / "etc" / "upper").mkdir(parents=True)
    (data / "var" / "lib").mkdir(parents=True)
    e = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_LOG=str(log),
             PENGYOS_BACKUP_NS_CACHE=str(tmp_path / "run" / "pengyos-backup" / "cache"))
    e.pop("RESTIC_REPOSITORY", None)

    class Env:
        pass

    x = Env()
    x.root, x.bindir, x.log, x.data, x.env, x.tmp = root, bindir, log, data, e, tmp_path

    def run(*args, extra_env=None, input=None):
        r = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *args],
                           env={**e, **(extra_env or {})}, text=True, capture_output=True,
                           input=input)
        return r

    def calls(tool=None):
        if not log.exists():
            return []
        out = [json.loads(line) for line in log.read_text().splitlines()]
        return [c for c in out if tool is None or c["tool"] == tool]

    def set_jobs(**paths):
        """Point the shipped jobs at the sandbox paths (keeping everything else)."""
        for name, ps in paths.items():
            f = root / "etc" / "pengyos" / "backup.d" / f"{name}.toml"
            text = f.read_text()
            import re
            text = re.sub(r"(?m)^paths = .*$", "paths = " + json.dumps(ps), text)
            f.write_text(text)

    def ready(url="/mnt/usb/backup"):
        (root / "etc" / "pengyos" / "backup").mkdir(parents=True, exist_ok=True)
        pw = root / "etc" / "pengyos" / "backup" / "password"
        pw.write_text("secret\n")
        pw.chmod(0o600)
        t = (root / "etc" / "pengyos" / "backup.toml")
        t.write_text(t.read_text().replace('url = ""', f'url = "{url}"'))

    x.run, x.calls, x.set_jobs, x.ready = run, calls, set_jobs, ready
    return x


# --- the shipped configuration -------------------------------------------------------

def test_shipped_config_parses_and_jobs_are_off():
    import tomllib
    cfg = tomllib.loads((OVERLAY / "etc/pengyos/backup.toml").read_text())
    assert cfg["repository"]["url"] == ""
    assert cfg["keep"] == {"daily": 7, "weekly": 4, "monthly": 12}
    for name in ("system", "home"):
        job = tomllib.loads((OVERLAY / f"etc/pengyos/backup.d/{name}.toml").read_text())
        assert job["enabled"] is False
        assert job["snapshot"] is True
        assert job["paths"]
    home = tomllib.loads((OVERLAY / "etc/pengyos/backup.d/home.toml").read_text())
    assert "/home/*/.cache" in home["exclude"]
    assert ".nobackup" in home["exclude_if_present"]
    system = tomllib.loads((OVERLAY / "etc/pengyos/backup.d/system.toml").read_text())
    assert system["paths"][0] == "/data/etc/upper"  # the overlay's changes, not the image's /etc


def test_script_is_executable_stdlib_python():
    assert os.access(SCRIPT, os.X_OK)
    src = SCRIPT.read_text()
    assert src.startswith("#!/usr/bin/env python3")
    imports = {l.split()[1].split(".")[0] for l in src.splitlines()
               if l.startswith(("import ", "from ")) and "__future__" not in l}
    assert imports <= set(sys.stdlib_module_names)


# --- setup -----------------------------------------------------------------------------

def test_run_without_a_repository_is_a_usage_error(env):
    r = env.run("run", "home")
    assert r.returncode == 2
    assert "pengyos-backup init --repo" in r.stderr


def test_init_local_makes_a_private_password_and_creates_the_repo(env):
    r = env.run("init", "--repo", "/mnt/usb/backup")
    assert r.returncode == 0, r.stderr
    pw = env.root / "etc/pengyos/backup/password"
    assert stat.S_IMODE(pw.stat().st_mode) == 0o600
    assert len(pw.read_text().strip()) >= 32
    assert 'url = "/mnt/usb/backup"' in (env.root / "etc/pengyos/backup.toml").read_text()
    restic = [c["argv"] for c in env.calls("restic")]
    assert ["cat", "config"] in restic and ["init"] in restic
    assert "recovery kit" in r.stdout and pw.read_text().strip() in r.stdout


def test_init_keeps_an_existing_password_and_repository(env):
    env.ready()
    r = env.run("init", "--repo", "/mnt/usb/backup", extra_env={"FAKE_REPO_EXISTS": "1"})
    assert r.returncode == 0, r.stderr
    assert (env.root / "etc/pengyos/backup/password").read_text() == "secret\n"
    assert ["init"] not in [c["argv"] for c in env.calls("restic")]


def test_init_sftp_makes_a_key_and_explains_how_to_authorize_it(env):
    r = env.run("init", "--repo", "sftp:beholder@behoserver:/data/extra/backup/x",
                extra_env={"FAKE_FAIL": "init"})
    assert r.returncode == 1
    assert 'restrict,command="internal-sftp" ssh-ed25519 AAAAfake' in r.stdout
    assert stat.S_IMODE((env.root / "etc/pengyos/backup/ssh").stat().st_mode) == 0o700
    sftp = env.calls("restic")[0]["argv"]
    assert sftp[0] == "-o" and sftp[1].startswith("sftp.command=ssh ")
    assert "beholder@behoserver -s sftp" in sftp[1]
    assert "StrictHostKeyChecking=accept-new" in sftp[1] and "BatchMode=yes" in sftp[1]


def test_sftp_url_with_a_port(env):
    env.ready("sftp://me@host:2222//srv/b")
    env.set_jobs(home=[str(env.data / "home")])
    env.run("run", "home")
    opt = env.calls("restic")[0]["argv"][1]
    assert "ssh -p 2222 " in opt and " me@host -s sftp" in opt


def test_a_password_others_can_read_is_refused(env):
    env.ready()
    (env.root / "etc/pengyos/backup/password").chmod(0o644)
    r = env.run("snapshots")
    assert r.returncode == 2 and "readable by others" in r.stderr


# --- enable / disable ----------------------------------------------------------------------

def test_enable_needs_a_repository_then_turns_the_timer_on(env):
    assert env.run("enable", "home").returncode == 2
    env.ready()
    r = env.run("enable", "home")
    assert r.returncode == 0, r.stderr
    assert "enabled = true" in (env.root / "etc/pengyos/backup.d/home.toml").read_text()
    assert env.calls("systemctl")[-1]["argv"] == ["enable", "--now", "pengyos-backup.timer"]
    env.run("disable", "home")
    assert env.calls("systemctl")[-1]["argv"] == ["disable", "--now", "pengyos-backup.timer"]


def test_unknown_job(env):
    env.ready()
    r = env.run("enable", "nope")
    assert r.returncode == 2 and "no such job" in r.stderr


# --- run -------------------------------------------------------------------------------------

def test_run_snapshots_each_subvolume_and_reads_it_in_a_private_namespace(env):
    env.ready()
    home = str(env.data / "home")
    upper = str(env.data / "etc" / "upper")
    varlib = str(env.data / "var" / "lib")
    env.set_jobs(home=[home], system=[upper, varlib])
    ids = {str(env.data): "5", home: "257", str(env.data / "etc"): "258", str(env.data / "var"): "259"}
    r = env.run("run", "home", "system", extra_env={"FAKE_ROOTIDS": json.dumps(ids)})
    assert r.returncode == 0, r.stderr
    btrfs = [c["argv"] for c in env.calls("btrfs")]
    snaps = [a for a in btrfs if a[:2] == ["subvolume", "snapshot"]]
    # one read-only snapshot per subvolume: home, etc (for etc/upper), var (for var/lib)
    assert [a[3] for a in snaps] == [home, str(env.data / "etc"), str(env.data / "var")]
    assert all(a[2] == "-r" for a in snaps)
    assert all("/data/.pengyos-backup-snap/" in a[4] for a in snaps)
    # every snapshot is deleted again
    assert sorted(a[-1] for a in btrfs if a[:2] == ["subvolume", "delete"]) == sorted(a[4] for a in snaps)
    # restic ran through unshare with private propagation, the snapshots bound over the real paths
    un = env.calls("unshare")
    assert un and un[0]["argv"][:4] == ["--mount", "--propagation", "private", "--"]
    mounts = [c["argv"] for c in env.calls("mount")]
    assert ["--bind", snaps[0][4], home] in mounts
    # the writable cache is bound out of the way first and restic told to use it
    # (a snapshot of /var would otherwise make /var/cache read-only: found on behothinkpad)
    assert mounts[0][0] == "--bind" and mounts[0][2].endswith("run/pengyos-backup/cache")
    assert backup_env_cache(env).endswith("run/pengyos-backup/cache")
    backup = [c for c in env.calls("restic") if "backup" in c["argv"]]
    assert len(backup) == 2
    a = backup[0]["argv"]
    assert a[:6] == ["backup", "--json", "--tag", "job:home", "--host", a[5]]
    assert a[-1] == home
    assert "--exclude-if-present" in a and "--exclude-larger-than" in a
    assert backup[0]["env"]["RESTIC_REPOSITORY"] == "/mnt/usb/backup"
    assert backup[0]["env"]["RESTIC_PASSWORD_FILE"].endswith("etc/pengyos/backup/password")
    st = json.loads((env.root / "var/lib/pengyos-backup/state.json").read_text())
    assert st["jobs"]["home"]["ok"] is True
    assert st["jobs"]["home"]["snapshot"] == "abcdef1234567890"
    assert st["jobs"]["home"]["bytes_added"] == 4096
    assert not list((env.root / "data/.pengyos-backup-snap").glob("*"))


def test_a_path_not_on_btrfs_is_backed_up_live(env):
    env.ready()
    env.set_jobs(home=[str(env.data / "home")])
    r = env.run("run", "home", extra_env={"FAKE_ROOTIDS": "{}"})
    assert r.returncode == 0, r.stderr
    assert "not on btrfs" in r.stderr
    assert not env.calls("unshare")
    assert [c for c in env.calls("restic") if "backup" in c["argv"]]


def test_missing_paths_are_skipped_and_a_job_with_none_fails(env):
    env.ready()
    env.set_jobs(home=[str(env.data / "nope")])
    r = env.run("run", "home")
    assert r.returncode == 1
    assert "does not exist" in r.stderr


def test_a_failed_backup_alerts_and_cleans_up(env):
    env.ready()
    home = str(env.data / "home")
    env.set_jobs(home=[home])
    r = env.run("run", "home", extra_env={"FAKE_FAIL": "backup",
                                          "FAKE_ROOTIDS": json.dumps({home: "257"})})
    assert r.returncode == 1
    al = env.calls("pengyos-alert")
    assert al and al[0]["argv"][:2] == ["send", "--title"]
    # never succeeded: urgent
    assert al[0]["argv"][al[0]["argv"].index("--priority") + 1] == "urgent"
    assert "--key" in al[0]["argv"] and "backup:home" in al[0]["argv"]
    dels = [c["argv"] for c in env.calls("btrfs") if c["argv"][:2] == ["subvolume", "delete"]]
    assert len(dels) == 1
    st = json.loads((env.root / "var/lib/pengyos-backup/state.json").read_text())
    assert st["jobs"]["home"]["ok"] is False and "failed" in st["jobs"]["home"]["error"]


def test_a_recent_success_makes_the_alert_high_not_urgent(env):
    env.ready()
    home = str(env.data / "home")
    env.set_jobs(home=[home])
    env.run("run", "home")
    env.log.unlink()
    env.run("run", "home", extra_env={"FAKE_FAIL": "backup"})
    al = env.calls("pengyos-alert")[0]["argv"]
    assert al[al.index("--priority") + 1] == "high"


def test_scheduled_run_applies_retention_and_a_due_check(env):
    env.ready()
    env.set_jobs(home=[str(env.data / "home")])
    env.run("enable", "home")
    r = env.run("run", "--scheduled")
    assert r.returncode == 0, r.stderr
    restic = [c["argv"] for c in env.calls("restic")]
    forget = next(a for a in restic if a[0] == "forget")
    assert forget[:4] == ["forget", "--prune", "--host", forget[3]]
    assert ["--keep-daily", "7"] == forget[forget.index("--keep-daily"):forget.index("--keep-daily") + 2]
    assert "--group-by" in forget
    assert ["check", "--read-data-subset", "5%"] in restic
    # the check is not due again the next night
    env.log.unlink()
    env.run("run", "--scheduled")
    assert not [c for c in env.calls("restic") if c["argv"][0] == "check"]


def test_scheduled_run_with_nothing_enabled_does_nothing(env):
    env.ready()
    r = env.run("run", "--scheduled")
    assert r.returncode == 0 and "nothing to do" in r.stdout
    assert not env.calls("restic")


def test_a_second_run_is_refused_while_one_holds_the_lock(env):
    import fcntl
    env.ready()
    env.set_jobs(home=[str(env.data / "home")])
    lock = env.root / "run" / "pengyos-backup.lock"
    lock.parent.mkdir(parents=True)
    with open(lock, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        r = env.run("run", "home")
    assert r.returncode == 2 and "already running" in r.stderr


def test_backend_keys_come_from_the_private_env_file(env):
    env.ready("s3:s3.us-west-004.backblazeb2.com/bucket/x")
    f = env.root / "etc/pengyos/backup/env"
    f.write_text("# B2 application key\nAWS_ACCESS_KEY_ID=keyid\n")
    f.chmod(0o600)
    env.run("snapshots")
    assert env.calls("restic")[0]["env"]["AWS_ACCESS_KEY_ID"] == "keyid"
    assert "-o" not in env.calls("restic")[0]["argv"]  # no sftp command for s3


# --- reading -------------------------------------------------------------------------------

def test_status_json_shape(env):
    env.ready()
    env.set_jobs(home=[str(env.data / "home")])
    env.run("run", "home")
    r = env.run("status", "--json")
    out = json.loads(r.stdout)
    assert out["repository"] == "/mnt/usb/backup"
    home = next(j for j in out["jobs"] if j["name"] == "home")
    assert home["ok"] is True and home["age_hours"] is not None and home["snapshot"]
    system = next(j for j in out["jobs"] if j["name"] == "system")
    assert system["ok"] is None and system["enabled"] is False


def test_snapshots_filters_by_job_tag(env):
    env.ready()
    r = env.run("snapshots", "home")
    assert r.returncode == 0
    a = env.calls("restic")[0]["argv"]
    assert a[:2] == ["snapshots", "--json"] and a[-2:] == ["--tag", "job:home"]
    assert "abcdef12" in r.stdout


def test_restore_defaults_to_a_new_directory_not_in_place(env):
    env.ready()
    r = env.run("restore", "abcdef12", "--path", "/home/me/file.txt")
    assert r.returncode == 0, r.stderr
    a = env.calls("restic")[0]["argv"]
    assert a[:2] == ["restore", "abcdef12"]
    assert a[a.index("--target") + 1].startswith("/var/tmp/pengyos-restore-")
    assert a[-2:] == ["--include", "/home/me/file.txt"]
    env.log.unlink()
    env.run("restore", "abcdef12", "--in-place")
    a = env.calls("restic")[0]["argv"]
    assert a[a.index("--target") + 1] == "/"


def test_kit_prints_the_repository_and_password(env):
    env.ready()
    r = env.run("kit")
    assert "/mnt/usb/backup" in r.stdout and "secret" in r.stdout


def backup_env_cache(env):
    return next(c for c in env.calls("restic") if "backup" in c["argv"])["env"]["RESTIC_CACHE_DIR"]


# --- the image wiring ------------------------------------------------------------------------

def test_service_runs_the_scheduled_command_and_alerts_on_failure():
    unit = (OVERLAY / "etc/systemd/system/pengyos-backup.service").read_text()
    assert "ExecStart=/usr/bin/pengyos-backup run --scheduled" in unit
    assert "OnFailure=pengyos-alert@%n.service" in unit
    timer = (OVERLAY / "etc/systemd/system/pengyos-backup.timer").read_text()
    assert "OnCalendar=*-*-* 02:30:00" in timer and "Persistent=yes" in timer
    preset = (OVERLAY / "usr/lib/systemd/system-preset/10-pengyos-server.preset").read_text()
    assert "disable pengyos-backup.timer" in preset


def test_the_tar_backup_is_gone():
    assert not (OVERLAY / "usr/libexec/pengyos/pengyos-backup.sh").exists()
    assert not (OVERLAY / "etc/pengyos/backup.conf").exists()


def test_restic_is_in_the_server_layer_and_checked():
    layer = (REPO_ROOT / "buildroot-external/configs/layers/server.config").read_text()
    assert "\nBR2_PACKAGE_RESTIC=y\n" in layer
    feats = (REPO_ROOT / "buildroot-external/board/server/features-required.txt").read_text()
    assert "usr/bin/restic" in feats and "usr/bin/pengyos-backup" in feats


# --- the real restic, when there is one -------------------------------------------------------

@pytest.mark.skipif(shutil.which("restic") is None, reason="no restic on PATH")
def test_real_restic_backs_up_and_restores(tmp_path):
    root = tmp_path / "root"
    shutil.copytree(OVERLAY / "etc" / "pengyos", root / "etc" / "pengyos",
                    ignore=shutil.ignore_patterns("alert*"))
    src = tmp_path / "src"
    (src / "docs").mkdir(parents=True)
    (src / "docs" / "a.txt").write_text("keep me")
    (src / "skip").mkdir()
    (src / "skip" / ".nobackup").write_text("")
    (src / "skip" / "b.txt").write_text("not me")
    job = root / "etc/pengyos/backup.d/home.toml"
    job.write_text(job.read_text().replace('paths = ["/home", "/root"]', f'paths = ["{src}"]')
                   .replace("snapshot = true", "snapshot = false"))
    env = dict(os.environ, PATH=os.environ["PATH"])
    run = lambda *a: subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *a],
                                    env=env, text=True, capture_output=True)
    repo = tmp_path / "repo"
    r = run("init", "--repo", str(repo))
    assert r.returncode == 0, r.stderr
    r = run("run", "home")
    assert r.returncode == 0, r.stderr
    snaps = json.loads(run("snapshots", "--json").stdout)
    assert len(snaps) == 1 and "job:home" in snaps[0]["tags"]
    out = tmp_path / "out"
    r = run("restore", snaps[0]["short_id"], "--target", str(out))
    assert r.returncode == 0, r.stderr
    assert (out / str(src).lstrip("/") / "docs/a.txt").read_text() == "keep me"
    assert not (out / str(src).lstrip("/") / "skip/b.txt").exists()
    assert run("check", "--read-data", "100%").returncode == 0
