"""pengy-vmm, the web page over pengyos-vm, served for real on a free port.

Its commands go to the real pengyos-vm script in the same sandbox as
test_pengyos_vm.py (`--vm-cmd "python3 pengyos-vm --root DIR"`), so a button
here is checked down to the systemctl call it ends in.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
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
        self.novnc = box.home.parent / "novnc"
        (self.novnc / "core").mkdir(parents=True)
        (self.novnc / "core" / "rfb.js").write_text("export default class RFB {}\n")
        self.proc = subprocess.Popen([sys.executable, str(VMM), "--vm-cmd", cmd,
                                      "--novnc-dir", str(self.novnc), "serve",
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


# --- 2026-11-27: ISO library, image picker, snapshots, the screen ------------

def _iso(n: int = 0x9000) -> bytes:
    data = bytearray(n)
    data[0x8001:0x8006] = b"CD001"
    return bytes(data)


def _put(web: Web, name: str, data: bytes, token: str | None = None):
    r = urllib.request.Request(f"http://127.0.0.1:{web.port}/api/isos/{name}", data=data, method="PUT")
    r.add_header("X-Token", token or web.token)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _wait_jobs(web: Web) -> list:
    for _ in range(150):
        jobs = json.loads(web.req("/api/jobs")[1])
        if jobs and all(j["done"] for j in jobs):
            return jobs
        time.sleep(0.1)
    raise AssertionError(jobs)


def test_iso_upload_goes_into_the_library(web: Web):
    code, body = _put(web, "debian-netinst.iso", _iso())
    assert code == 200 and body["ok"], body
    lib = json.loads(web.req("/api/isos")[1])["isos"]
    assert [i["name"] for i in lib] == ["debian-netinst.iso"]
    vms = web.box.home / "vms"
    assert not [p for p in vms.iterdir() if p.name.startswith(".pengy-vmm-upload")]
    # a VM from the library by name, then removing what it uses is refused
    code, _, _ = web.req("/api/vms", {"name": "deb", "kind": "iso", "source": "debian-netinst.iso"})
    assert code == 202 and _wait_jobs(web)[0]["ok"]
    code, body, _ = web.req("/api/isos/debian-netinst.iso/remove", {})
    assert code == 400 and "attached to deb" in json.loads(body)["output"]


def test_iso_upload_refusals_keep_nothing(web: Web):
    assert _put(web, "x.iso", _iso(), token="wrong")[0] == 403
    assert _put(web, "..%2Fescape.iso", _iso())[0] == 400
    code, body = _put(web, "page.iso", b"<html>404</html>" * 100)
    assert code == 400 and "not an ISO image" in body["output"]
    vms = web.box.home / "vms"
    assert not [p for p in vms.rglob("*") if p.is_file()]


def test_iso_download_job_reports_progress(web: Web, tmp_path: Path):
    import functools
    import http.server
    www = tmp_path / "www"
    www.mkdir()
    (www / "alpine.iso").write_bytes(_iso(2 << 20))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(www))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/alpine.iso"
        assert web.req("/api/isos", {"url": url})[0] == 202
        job = _wait_jobs(web)[0]
        assert job["ok"] and job["progress"]["done"] == 2 << 20 and "added alpine.iso" in job["output"]
        assert web.req("/api/isos", {"url": "file:///etc/passwd"})[0] == 400
    finally:
        srv.shutdown()


def test_image_picker_lists_vms_images(web: Web):
    d = web.box.home / "vms" / "images"
    d.mkdir(parents=True)
    (d / "debian-13.qcow2").write_text("{}")
    (d / "notes.txt").write_text("no")
    imgs = json.loads(web.req("/api/images")[1])["images"]
    assert [i["name"] for i in imgs] == ["debian-13.qcow2"]


def test_snapshot_buttons(web: Web):
    web.box.run("create", "deb", "--iso", str(web.box.iso))
    r = json.loads(web.req("/api/vms/deb/snapshot-create", {"label": "before it"})[1])
    assert r["ok"] and "s1 (before it)" in r["output"]
    snaps = json.loads(web.req("/api/vms/deb/snapshots")[1])["snapshots"]
    assert [(x["name"], x["label"]) for x in snaps] == [("s1", "before it")]
    assert json.loads(web.req("/api/vms/deb")[1])["snapshots"][0]["name"] == "s1"
    assert json.loads(web.req("/api/vms/deb/snapshot-revert", {"snap": "s1"})[1])["ok"]
    assert web.req("/api/vms/deb/snapshot-revert", {"snap": "s1; rm -rf ~"})[0] == 400
    assert json.loads(web.req("/api/vms/deb/snapshot-delete", {"snap": "s1"})[1])["ok"]


def test_novnc_is_served_but_nothing_else(web: Web):
    code, body, headers = web.req("/novnc/core/rfb.js")
    assert code == 200 and "class RFB" in body and headers["Content-Type"].startswith("text/javascript")
    assert web.req("/novnc/core/rfb.js", token=None)[0] == 403
    assert web.req("/novnc/../.config/pengy-vmm/token")[0] == 404
    assert web.req("/novnc/%2e%2e/%2e%2e/home/.config/pengy-vmm/token")[0] == 404
    code, page, headers = web.req("/screen/deb")
    assert code == 200 and 'import RFB from "/novnc/core/rfb.js"' in page and "/api/vms/deb/vnc" in page
    assert "script-src 'self'" in headers["Content-Security-Policy"]


def _ws_open(web: Web, name: str, origin: str | None = None, cookie: bool = True):
    s = socket.create_connection(("127.0.0.1", web.port), 5)
    key = base64.b64encode(os.urandom(16)).decode()
    host = f"127.0.0.1:{web.port}"
    lines = [f"GET /api/vms/{name}/vnc HTTP/1.1", f"Host: {host}", "Upgrade: websocket",
             "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
             "Sec-WebSocket-Protocol: binary", f"Origin: {origin or 'http://' + host}"]
    if cookie:
        lines.append(f"Cookie: vmm={web.token}")
    s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
    f = s.makefile("rb")
    status = f.readline().decode()
    headers = {}
    while (line := f.readline().decode().strip()):
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    return s, f, status, headers, key


def _ws_frame(f) -> tuple[int, bytes]:
    head = f.read(2)
    n = head[1] & 0x7F
    if n == 126:
        n = int.from_bytes(f.read(2), "big")
    elif n == 127:
        n = int.from_bytes(f.read(8), "big")
    return head[0] & 0x0F, f.read(n)


def test_screen_websocket_relays_to_vnc_sock(web: Web):
    web.box.run("create", "deb", "--iso", str(web.box.iso))
    path = web.box.home / "vms" / "deb" / "vnc.sock"
    got = []
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    srv.listen(1)

    def qemu():
        c, _ = srv.accept()
        c.sendall(b"RFB 003.008\n")
        got.append(c.recv(100))
        c.sendall(b"x" * 70000)       # a frame over 64 KiB (8-byte length)
        c.close()

    threading.Thread(target=qemu, daemon=True).start()
    s, f, status, headers, key = _ws_open(web, "deb")
    try:
        assert " 101 " in status, status
        want = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        assert headers["sec-websocket-accept"] == want and headers["sec-websocket-protocol"] == "binary"
        assert _ws_frame(f) == (0x2, b"RFB 003.008\n")
        mask = os.urandom(4)
        msg = b"RFB 003.008\n"
        s.sendall(bytes([0x82, 0x80 | len(msg)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(msg)))
        # A stream: 70000 bytes may come as several frames (the first of 64 KiB
        # needs the 8-byte length), then QEMU's close becomes a close frame.
        total, sizes = b"", []
        while True:
            op, data = _ws_frame(f)
            if op != 0x2:
                break
            total += data
            sizes.append(len(data))
        assert op == 0x8 and total == b"x" * 70000 and max(sizes) >= 1 << 16
        assert got == [msg]
    finally:
        s.close()
        srv.close()


def test_screen_websocket_refusals(web: Web):
    web.box.run("create", "deb", "--iso", str(web.box.iso))
    assert " 409 " in _ws_open(web, "deb")[2]                       # not running: no socket
    assert " 403 " in _ws_open(web, "deb", origin="http://evil.example")[2]
    assert " 403 " in _ws_open(web, "deb", cookie=False)[2]
