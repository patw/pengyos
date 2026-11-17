"""Tests for the `pengyos-alert` shipped tool, and the image wiring around it.

The shipped script is run as a program against a sandbox root (`--root DIR`)
with a fake `logger` (the journal) and a fake `systemctl`. The transports are
real: an HTTP server on 127.0.0.1 stands in for ntfy and a minimal SMTP server
for the mail relay, so what goes over the wire is what is asserted.

Also checked here, because the tool is only useful wired: the OnFailure= lines
on the Server's own units, smartd.conf's exec hook, and pengyos-monitor.sh's
disk-full alert (run for real with a fake `df` and a fake `pengyos-alert`).

That smartd and systemd really call it is proven by booting the Server image
(spec.md, "Verification"). No build, no network beyond loopback, no root.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_DIR.parents[1]
OVERLAY = REPO_ROOT / "buildroot-external" / "board" / "server" / "rootfs-overlay"
SCRIPT = OVERLAY / "usr" / "bin" / "pengyos-alert"
MONITOR = OVERLAY / "usr" / "libexec" / "pengyos" / "pengyos-monitor.sh"


# --- fakes ----------------------------------------------------------------------

@pytest.fixture()
def root(tmp_path: Path) -> Path:
    bindir = tmp_path / "usr" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "logger").write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$(dirname \"$0\")/../../journal.log\"\n")
    (bindir / "systemctl").write_text(
        "#!/bin/sh\necho \"x $6 - Fake unit\"\necho '   Active: failed (Result: exit-code)'\n"
        "echo 'Nov 17 02:00:01 host backup[1]: tar: /mnt/backup: No such file or directory'\n")
    for f in bindir.iterdir():
        f.chmod(0o755)
    (tmp_path / "etc" / "pengyos").mkdir(parents=True)
    (tmp_path / "etc" / "hostname").write_text("lidserver\n")
    return tmp_path


def conf(root: Path, text: str) -> None:
    (root / "etc/pengyos/alert.conf").write_text(text)


def cred(root: Path, text: str, mode: int = 0o600) -> None:
    p = root / "etc/pengyos/alert.cred"
    p.write_text(text)
    p.chmod(mode)


def run(root: Path, *args: str, stdin: str | None = None, env: dict | None = None):
    e = dict(os.environ)
    for k in list(e):
        if k.startswith("SMARTD_"):
            del e[k]
    e.update(env or {})
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *args],
                          capture_output=True, text=True, input=stdin, env=e, timeout=60)


def journal(root: Path) -> str:
    p = root / "journal.log"
    return p.read_text() if p.exists() else ""


class Ntfy:
    """An HTTP server that records each POST (path, headers, body)."""

    def __init__(self, status: int = 200):
        self.posts: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                outer.posts.append({"path": self.path, "headers": dict(self.headers),
                                    "body": self.rfile.read(n).decode()})
                self.send_response(status)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}/pengyos-test"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


class Smtp:
    """Just enough SMTP for smtplib: EHLO (offers AUTH PLAIN), AUTH, MAIL, RCPT, DATA."""

    def __init__(self):
        self.messages: list[dict] = []
        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                msg = {"auth": None, "rcpt": [], "data": ""}
                self.wfile.write(b"220 sink ESMTP\r\n")
                while True:
                    line = self.rfile.readline().decode().rstrip("\r\n")
                    if not line:
                        return
                    verb = line.split(" ", 1)[0].upper()
                    if verb == "EHLO":
                        self.wfile.write(b"250-sink\r\n250 AUTH PLAIN\r\n")
                    elif verb == "AUTH":
                        raw = base64.b64decode(line.split()[2]).split(b"\0")
                        msg["auth"] = (raw[1].decode(), raw[2].decode())
                        self.wfile.write(b"235 ok\r\n")
                    elif verb == "RCPT":
                        msg["rcpt"].append(line)
                        self.wfile.write(b"250 ok\r\n")
                    elif verb == "DATA":
                        self.wfile.write(b"354 go\r\n")
                        buf = []
                        while True:
                            d = self.rfile.readline().decode()
                            if d in (".\r\n", ""):
                                break
                            buf.append(d)
                        msg["data"] = "".join(buf)
                        outer.messages.append(msg)
                        self.wfile.write(b"250 queued\r\n")
                    elif verb == "QUIT":
                        self.wfile.write(b"221 bye\r\n")
                        return
                    else:
                        self.wfile.write(b"250 ok\r\n")

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture()
def ntfy():
    s = Ntfy()
    yield s
    s.close()


@pytest.fixture()
def smtp():
    s = Smtp()
    yield s
    s.close()


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --- status / nothing configured -------------------------------------------------

def test_status_unconfigured(root):
    r = run(root, "status")
    assert r.returncode == 0
    assert "none configured (journal only)" in r.stdout
    j = json.loads(run(root, "status", "--json").stdout)
    assert j["transports"] == [] and j["last_sent"] is None


def test_send_without_transport_goes_to_the_journal(root):
    r = run(root, "send", "backup", "disk", "missing")
    assert r.returncode == 0, r.stderr
    assert "journal only" in r.stdout
    assert "-t pengyos-alert" in journal(root) and "backup disk missing" in journal(root)
    assert journal(root).count("backup disk missing") == 1  # the title is not repeated


def test_test_with_nothing_configured_fails(root):
    r = run(root, "test")
    assert r.returncode == 1 and "nothing to test" in r.stderr


def test_empty_message_is_misuse(root):
    r = run(root, "send", "-", stdin="  \n")
    assert r.returncode == 2


# --- ntfy ----------------------------------------------------------------------------

def test_ntfy_send_with_token(root, ntfy):
    conf(root, f"NTFY_URL={ntfy.url}\n")
    cred(root, "NTFY_TOKEN=tk_secret\n")
    r = run(root, "send", "--title", "lidserver: hello", "--priority", "urgent", "the body")
    assert r.returncode == 0, r.stderr
    [p] = ntfy.posts
    assert p["path"] == "/pengyos-test" and p["body"] == "the body"
    assert p["headers"]["Title"] == "lidserver: hello"
    assert p["headers"]["Priority"] == "5"
    assert p["headers"]["Authorization"] == "Bearer tk_secret"
    assert "user.crit" in journal(root)


def test_test_command_reaches_ntfy(root, ntfy):
    conf(root, f"NTFY_URL={ntfy.url}\n")
    assert run(root, "test").returncode == 0
    assert ntfy.posts[0]["headers"]["Title"] == "lidserver: test alert"
    assert run(root, "test").returncode == 0  # never de-duplicated
    assert len(ntfy.posts) == 2
    assert json.loads(run(root, "status", "--json").stdout)["last_sent"]


def test_readable_cred_file_is_refused(root, ntfy):
    conf(root, f"NTFY_URL={ntfy.url}\n")
    cred(root, "NTFY_TOKEN=tk_secret\n", mode=0o644)
    r = run(root, "send", "x")
    assert r.returncode == 0
    assert "readable by others" in r.stderr
    assert "Authorization" not in ntfy.posts[0]["headers"]


def test_secret_in_conf_is_ignored(root, ntfy):
    conf(root, f"NTFY_URL={ntfy.url}\nNTFY_TOKEN=leaky\n")
    r = run(root, "send", "x")
    assert "ignored" in r.stderr
    assert "Authorization" not in ntfy.posts[0]["headers"]
    assert "leaky" not in run(root, "status", "--json").stdout


def test_non_latin1_title_is_encoded(root, ntfy):
    conf(root, f"NTFY_URL={ntfy.url}\n")
    assert run(root, "send", "--title", "disque plein — /data", "x").returncode == 0
    assert ntfy.posts[0]["headers"]["Title"].startswith("=?UTF-8?B?")


def test_http_error_fails(root):
    s = Ntfy(status=403)
    try:
        conf(root, f"NTFY_URL={s.url}\n")
        r = run(root, "send", "x")
        assert r.returncode == 1 and "HTTP 403" in r.stderr
        assert "ntfy failed" in journal(root)
    finally:
        s.close()


def test_stdin_message(root, ntfy):
    conf(root, f"NTFY_URL={ntfy.url}\n")
    assert run(root, "send", "-", stdin="line one\nline two\n").returncode == 0
    assert ntfy.posts[0]["body"] == "line one\nline two"
    assert ntfy.posts[0]["headers"]["Title"] == "lidserver: line one"


# --- mail ----------------------------------------------------------------------------

def test_mail_with_login(root, smtp):
    conf(root, f"MAIL_TO=pat@example.com\nSMTP_HOST=127.0.0.1\nSMTP_PORT={smtp.port}\n"
               "SMTP_TLS=none\nSMTP_USER=pat\n")
    cred(root, "SMTP_PASSWORD=hunter22\n")
    r = run(root, "send", "--title", "lidserver: disk", "--priority", "high", "sda is dying")
    assert r.returncode == 0, r.stderr
    [m] = smtp.messages
    assert m["auth"] == ("pat", "hunter22")
    assert "pat@example.com" in m["rcpt"][0]
    assert "Subject: lidserver: disk" in m["data"]
    assert "From: pengyos-alert@lidserver" in m["data"]
    assert "X-Priority: 2" in m["data"] and "sda is dying" in m["data"]


def test_one_transport_down_is_still_sent(root, smtp):
    conf(root, f"NTFY_URL=http://127.0.0.1:{closed_port()}/t\n"
               f"MAIL_TO=pat@example.com\nSMTP_HOST=127.0.0.1\nSMTP_PORT={smtp.port}\nSMTP_TLS=none\n")
    r = run(root, "send", "x")
    assert r.returncode == 0
    assert "ntfy failed" in r.stderr and "sent by mail" in r.stdout


def test_bad_tls_mode_fails(root):
    conf(root, "MAIL_TO=a@b\nSMTP_HOST=127.0.0.1\nSMTP_TLS=maybe\n")
    r = run(root, "send", "x")
    assert r.returncode == 1 and "SMTP_TLS" in r.stderr


# --- de-duplication, unit-failed, smartd --------------------------------------------

def test_unit_failed_is_deduplicated(root, ntfy):
    conf(root, f"NTFY_URL={ntfy.url}\n")
    r = run(root, "unit-failed", "pengyos-backup.service")
    assert r.returncode == 0, r.stderr
    [p] = ntfy.posts
    assert p["headers"]["Title"] == "lidserver: pengyos-backup.service failed"
    assert p["headers"]["Priority"] == "4"
    assert "No such file or directory" in p["body"]
    assert "journalctl -u pengyos-backup.service -b" in p["body"]
    r = run(root, "unit-failed", "pengyos-backup.service")
    assert r.returncode == 0 and "not sent" in r.stdout
    assert len(ntfy.posts) == 1
    assert "suppressed" in journal(root)
    # another unit is another key
    run(root, "unit-failed", "pengyos-monitor.service")
    assert len(ntfy.posts) == 2
    # REPEAT_HOURS=0: every failure goes out
    conf(root, f"NTFY_URL={ntfy.url}\nREPEAT_HOURS=0\n")
    run(root, "unit-failed", "pengyos-backup.service")
    assert len(ntfy.posts) == 3


def test_failed_send_is_not_recorded(root, ntfy):
    conf(root, f"NTFY_URL=http://127.0.0.1:{closed_port()}/t\n")
    assert run(root, "send", "--key", "k", "x").returncode == 1
    conf(root, f"NTFY_URL={ntfy.url}\n")
    assert run(root, "send", "--key", "k", "x").returncode == 0
    assert len(ntfy.posts) == 1  # the failure did not start the repeat window


def test_bad_repeat_hours_is_misuse(root, ntfy):
    conf(root, f"NTFY_URL={ntfy.url}\nREPEAT_HOURS=soon\n")
    assert run(root, "send", "--key", "k", "x").returncode == 2


def test_smartd_health_is_urgent(root, ntfy):
    conf(root, f"NTFY_URL={ntfy.url}\n")
    env = {"SMARTD_DEVICE": "/dev/sda", "SMARTD_DEVICESTRING": "/dev/sda [SAT]",
           "SMARTD_FAILTYPE": "Health",
           "SMARTD_FULLMESSAGE": "Device: /dev/sda [SAT], FAILED SMART self-check. BACK UP DATA NOW!"}
    r = run(root, "smartd", "root", env=env)  # smartd passes the -m address as an argument
    assert r.returncode == 0, r.stderr
    [p] = ntfy.posts
    assert p["headers"]["Title"] == "lidserver: disk warning on /dev/sda [SAT] (Health)"
    assert p["headers"]["Priority"] == "5"
    assert "BACK UP DATA NOW" in p["body"] and "smartctl -a /dev/sda" in p["body"]


def test_smartd_email_test_is_never_deduplicated(root, ntfy):
    conf(root, f"NTFY_URL={ntfy.url}\n")
    env = {"SMARTD_DEVICE": "/dev/nvme0", "SMARTD_FAILTYPE": "EmailTest",
           "SMARTD_MESSAGE": "TEST EMAIL from smartd for device: /dev/nvme0"}
    run(root, "smartd", env=env)
    run(root, "smartd", env=env)
    assert len(ntfy.posts) == 2 and ntfy.posts[0]["headers"]["Priority"] == "3"


@pytest.mark.skipif(os.geteuid() == 0, reason="the guard only applies to non-root")
def test_send_needs_root_without_test_hook():
    r = subprocess.run([sys.executable, str(SCRIPT), "send", "x"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "run as root" in r.stderr


# --- image wiring -------------------------------------------------------------------

ALERTING_UNITS = [
    OVERLAY / "etc/systemd/system/pengyos-backup.service",
    OVERLAY / "etc/systemd/system/pengyos-monitor.service",
    OVERLAY / "etc/systemd/system/pengyos-update-check.service",
    OVERLAY / "usr/lib/systemd/system/pengyos-update-auto.service",
]


@pytest.mark.parametrize("unit", ALERTING_UNITS, ids=lambda p: p.name)
def test_units_alert_on_failure(unit):
    text = unit.read_text()
    unit_section = text.split("[Service]")[0]
    assert "OnFailure=pengyos-alert@%n.service" in unit_section


def test_alert_unit_cannot_loop():
    text = (OVERLAY / "usr/lib/systemd/system/pengyos-alert@.service").read_text()
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    assert not any(ln.startswith("OnFailure=") for ln in lines)
    assert "ExecStart=/usr/bin/pengyos-alert unit-failed %i" in lines


def test_smartd_conf_hook_exists_and_is_executable():
    line = next(ln for ln in (OVERLAY / "etc/smartd.conf").read_text().splitlines()
                if ln.startswith("DEVICESCAN"))
    hook = line.split("-M exec", 1)[1].split()[0]
    path = OVERLAY / hook.lstrip("/")
    assert path.is_file() and os.access(path, os.X_OK)
    assert "pengyos-alert smartd" in path.read_text()
    assert os.access(SCRIPT, os.X_OK)


def test_shipped_conf_has_no_active_settings():
    """The shipped config only documents: nothing is sent until the owner sets it."""
    text = (OVERLAY / "etc/pengyos/alert.conf").read_text()
    active = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert active == []


# --- pengyos-monitor.sh's disk-full alert ---------------------------------------------

def monitor(tmp_path: Path, pct: int, threshold: str) -> tuple[str, list[str]]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "df").write_text(
        "#!/bin/sh\necho 'Filesystem 1024-blocks Used Available Capacity Mounted on'\n"
        f"echo '/dev/vda3 100 {pct} {100 - pct} {pct}% /'\n")
    calls = tmp_path / "alerts.txt"
    (bindir / "pengyos-alert").write_text(
        f"#!/bin/sh\nprintf '%s|' \"$@\" >> {calls}\necho >> {calls}\n")
    for f in bindir.iterdir():
        f.chmod(0o755)
    log = tmp_path / "monitor.log"
    cfg = tmp_path / "monitor.conf"
    cfg.write_text(f"LOG_FILE={log}\nDISK_TARGETS=/ /var\nDISK_ALERT_PCT={threshold}\n")
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    subprocess.run(["bash", str(MONITOR), str(cfg)], check=True, env=env,
                   capture_output=True, timeout=60)
    sent = calls.read_text().splitlines() if calls.exists() else []
    return log.read_text(), sent


def test_monitor_alerts_on_a_full_disk(tmp_path):
    line, sent = monitor(tmp_path, 95, "90")
    assert " disk_root=95% disk_root=95% " in line  # fields are space-separated now
    assert len(sent) == 2  # / and /var: same mount, same key; pengyos-alert dedups
    args = sent[0].split("|")
    assert args[:5] == ["send", "--priority", "high", "--key", "disk:/"]
    assert "/ is 95% full" in args[6]


@pytest.mark.parametrize("pct,threshold", [(50, "90"), (95, "0"), (95, "off")])
def test_monitor_quiet_below_threshold_or_off(tmp_path, pct, threshold):
    _, sent = monitor(tmp_path, pct, threshold)
    assert sent == []
