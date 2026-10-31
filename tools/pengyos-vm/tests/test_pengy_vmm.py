"""pengy-vmm, the web page over pengyos-vm, served for real on a free port.

Its commands go to the real pengyos-vm script in the same sandbox as
test_pengyos_vm.py (`--vm-cmd "python3 pengyos-vm --root DIR"`), so a button
here is checked down to the systemctl call it ends in.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from test_pengyos_vm import BOARD, SCRIPT, Box

VMM = BOARD / "server" / "rootfs-overlay" / "usr" / "bin" / "pengy-vmm"
UNIT = BOARD / "server" / "rootfs-overlay" / "usr" / "lib" / "systemd" / "user" / "pengy-vmm.service"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Web:
    def __init__(self, box: Box):
        self.box = box
        self.port = free_port()
        env = dict(os.environ, HOME=str(box.home))
        cmd = f"{sys.executable} {SCRIPT} --root {box.root}"
        self.proc = subprocess.Popen([sys.executable, str(VMM), "--vm-cmd", cmd, "serve",
                                      "--port", str(self.port)], env=env,
                                     stderr=subprocess.PIPE, text=True)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.1).close()
                break
            except OSError:
                time.sleep(0.1)
        self.token = (box.home / ".config" / "pengy-vmm" / "token").read_text().strip()

    def req(self, path: str, body=None, token: str | None = "header", query: bool = False):
        url = f"http://127.0.0.1:{self.port}{path}"
        if query:
            url += f"?token={self.token}"
        r = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                   method="GET" if body is None else "POST")
        if token == "header":
            r.add_header("X-Token", self.token)
            r.add_header("Cookie", f"vmm={self.token}")
        elif token:
            r.add_header("X-Token", token)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, resp.read().decode(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(), e.headers

    def close(self):
        self.proc.terminate()
        self.proc.wait(5)


@pytest.fixture()
def web(tmp_path: Path):
    w = Web(Box(tmp_path))
    yield w
    w.close()


def test_the_token_is_private_and_required(web: Web):
    path = web.box.home / ".config" / "pengy-vmm" / "token"
    assert path.stat().st_mode & 0o777 == 0o600 and len(web.token) >= 30
    assert web.req("/", token=None)[0] == 403
    assert web.req("/api/vms", token=None)[0] == 403
    code, page, headers = web.req("/", token=None, query=True)
    assert code == 200 and "pengy-vmm" in page
    assert "SameSite=Strict" in headers["Set-Cookie"] and "HttpOnly" in headers["Set-Cookie"]
    # A change needs the header, not just the cookie (a cross-site form has only the cookie).
    assert web.req("/api/vms/x/start", {}, token="wrong")[0] == 403


def test_create_list_and_buttons_go_through_pengyos_vm(web: Web):
    iso = web.box.iso
    code, body, _ = web.req("/api/vms", {"name": "deb", "kind": "iso", "source": str(iso),
                                          "memory": "1G", "forwards": "8080:80", "ssh_key": True})
    assert code == 202, body
    for _ in range(100):
        jobs = json.loads(web.req("/api/jobs")[1])
        if jobs and jobs[0]["done"]:
            break
        time.sleep(0.1)
    assert jobs[0]["ok"], jobs
    vms = json.loads(web.req("/api/vms")[1])["vms"]
    assert [v["name"] for v in vms] == ["deb"] and vms[0]["memory"] == "1G"
    assert vms[0]["forwards"] == ["127.0.0.1:8080->80"] and vms[0]["ssh_key"]
    assert json.loads(web.req("/api/vms/deb/start", {})[1])["ok"]
    assert ["--user", "start", "pengyos-vm@deb.service"] in web.box.calls("systemctl")
    assert json.loads(web.req("/api/vms/deb")[1])["state"] == "running"
    code, body, _ = web.req("/api/vms/deb/delete", {})
    assert code == 400 and "stop deb first" in json.loads(body)["output"]
    assert json.loads(web.req("/api/vms/deb/stop", {})[1])["ok"]
    assert json.loads(web.req("/api/vms/deb/delete", {})[1])["ok"]
    assert json.loads(web.req("/api/vms")[1])["vms"] == []


def test_bad_requests_are_refused(web: Web):
    code, body, _ = web.req("/api/vms", {"name": "Bad Name", "kind": "iso", "source": "/x"})
    assert code == 400 and "lowercase" in body
    assert web.req("/api/vms", {"name": "x", "kind": "iso", "source": ""})[0] == 400
    assert web.req("/api/vms/x/reboot-the-host", {})[0] == 404
    assert web.req("/api/vms/..%2f..%2fetc/start", {})[0] == 404


def test_console_tail_strips_terminal_noise(web: Web):
    web.req("/api/vms", {"name": "c", "kind": "iso", "source": str(web.box.iso)})
    for _ in range(100):
        if (web.box.home / "vms" / "c" / "vm.json").exists():
            break
        time.sleep(0.1)
    (web.box.home / "vms" / "c" / "console.log").write_bytes(
        b"\x1b]3008;start=abc;user=root\x1b\\# uname -r\r\n7.2.13\r\n\x1b[1;32mok\x1b[0m\r\n"
        b"\x1b[!p\x1bP+q6E616D65\x1b\\\x07done\r\n")
    text = json.loads(web.req("/api/vms/c/console")[1])["text"]
    assert text == "# uname -r\n7.2.13\nok\ndone"


def test_url_prints_the_token(tmp_path: Path):
    box = Box(tmp_path)
    out = subprocess.run([sys.executable, str(VMM), "url"], env=dict(os.environ, HOME=str(box.home)),
                         capture_output=True, text=True).stdout.strip()
    token = (box.home / ".config" / "pengy-vmm" / "token").read_text().strip()
    assert out == f"http://127.0.0.1:5100/?token={token}"


def test_the_unit_serves_it():
    assert "ExecStart=/usr/bin/pengy-vmm serve" in UNIT.read_text()
