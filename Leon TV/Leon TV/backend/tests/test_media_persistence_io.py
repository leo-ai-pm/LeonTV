"""Actual local HTTP streams behind DNS/connector seams, real IO validators."""
import hashlib
import os
import socket
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_image_policy import make_query
from test_image_receipts import image_bytes, raw_for

from core.creator.store import CreatorError, digest, utc_now
from core.execution import media_persistence_io as io
from core.execution.image_receipts import decode_query
from core.execution.policy import now


@contextmanager
def local_http(monkeypatch, mode="good"):
    calls = []
    content = image_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append({"path": self.path, "headers": dict(self.headers), "received_at": utc_now()})
            code = 404 if mode == "404" else 302 if mode == "redirect" else 200
            self.send_response(code)
            if mode == "redirect":
                self.send_header("Location", "https://media.example.com/second.png")
            if mode == "wrong_mime":
                self.send_header("Content-Type", "text/html")
            if mode == "encoded":
                self.send_header("Content-Encoding", "gzip")
            if mode != "oversize_stream":
                size = len(content) + 10 if mode == "truncated" else io.POLICY["max_bytes"] + 1 if mode == "oversize_header" else len(content)
                self.send_header("Content-Length", str(size))
            self.end_headers()
            try:
                if mode == "timeout":
                    time.sleep(0.2)
                if mode == "oversize_stream":
                    self.wfile.write(b"x" * (io.POLICY["max_bytes"] + 1))
                elif mode not in {"404", "redirect", "oversize_header"}:
                    self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    monkeypatch.setenv("D47_LOOPBACK_PORT", str(server.server_port))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Only DNS results and actual transport are synthetic. URL, address policy,
    # framing/byte/time limits, file IO and image verification remain production.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))])

    def connect(host, addresses, timeout):
        assert host == "media.example.com" and addresses[0][4] == ("8.8.8.8", 443)
        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(timeout)
        connection.connect(server.server_address)
        return connection

    monkeypatch.setattr(io, "connect_pinned", connect)
    try:
        yield calls, content
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("url", [
    "http://media.example.com/x", "https://media.example.com:8443/x", "https://u:p@media.example.com/x",
    "https://media.example.com/x#f", "https://127.0.0.1/x", "https://[::1]/x", "https://media.example.com./x",
    "https://media..example.com/x", "https://-bad.example/x", "https://media.example.com/%0a\n",
    "https://media.example.com\\@evil.example/x", "https://本地.example/x", "https://localhost/x ",
])
def test_unsafe_target_rejects_before_dns(url):
    with pytest.raises((CreatorError, ValueError)):
        io.target(url)


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"], ["::1"], ["8.8.8.8", "192.168.1.1"], ["224.0.0.1"]])
def test_every_resolved_address_must_be_public(monkeypatch, addresses):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 443)) for a in addresses])
    with pytest.raises(CreatorError):
        io.public_addresses("media.example.com")


def test_local_stream_one_request_no_proxy_key_and_exact_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("API_KEY", "SYNTHETIC-DO-NOT-SEND")
    with local_http(monkeypatch) as (calls, content):
        with io.PrivateMediaFiles(tmp_path.resolve(), "a" * 32) as files:
            fd = files.new_part()
            try:
                result = io.download_to_fd("https://media.example.com/result.png?exact=1", fd, seconds=1, maximum=io.POLICY["max_bytes"])
            finally:
                os.close(fd)
            assert result == {"ok": True, "http_status": 200, "sha256": hashlib.sha256(content).hexdigest(), "byte_length": len(content), "content_type": None}
            assert files.read(".part", io.POLICY["max_bytes"]) == content
            files.proof({"synthetic": True})
            files.publish()
            assert files.read(".blob", io.POLICY["max_bytes"]) == content
            assert not files.exists(".part")
        assert len(calls) == 1 and calls[0]["path"] == "/result.png?exact=1"
        assert not any(k.lower() in {"authorization", "proxy-authorization", "cookie"} for k in calls[0]["headers"])


@pytest.mark.parametrize("mode", ["404", "redirect", "oversize_header", "oversize_stream", "truncated", "encoded", "timeout", "wrong_mime"])
def test_local_failure_stream_never_follows_or_retries(tmp_path, monkeypatch, mode):
    with local_http(monkeypatch, mode) as (calls, _):
        with io.PrivateMediaFiles(tmp_path.resolve(), "b" * 32) as files:
            fd = files.new_part()
            try:
                if mode in {"404", "redirect"}:
                    result = io.download_to_fd("https://media.example.com/x", fd, seconds=1, maximum=io.POLICY["max_bytes"])
                    assert result["ok"] is False and result["http_status"] == (404 if mode == "404" else 302)
                else:
                    with pytest.raises(Exception):
                        io.download_to_fd("https://media.example.com/x", fd, seconds=0.05 if mode == "timeout" else 1, maximum=io.POLICY["max_bytes"])
            finally:
                os.close(fd)
            assert not files.exists(".blob") and not files.exists(".ready.json")
        assert len(calls) == 1


@pytest.mark.parametrize("attack", ["root_link", "part_link", "blob_link", "proof_link", "hardlink", "overwrite", "world_directory", "fifo"])
def test_private_storage_no_symlinks_hardlinks_overwrite_or_special_files(tmp_path, attack):
    root = tmp_path.resolve()
    outside = root / "outside"
    outside.write_bytes(b"original")
    outside.chmod(0o600)
    if attack == "root_link":
        link = root / "linked"
        link.symlink_to(root, target_is_directory=True)
        with pytest.raises(CreatorError):
            io.PrivateMediaFiles(link, "c" * 32)
        return
    with io.PrivateMediaFiles(root, "c" * 32) as files:
        directory = root / "media-persistence"
        if attack == "world_directory":
            directory.chmod(0o777)
            with pytest.raises(CreatorError):
                io.PrivateMediaFiles(root, "d" * 32)
        elif attack == "overwrite":
            fd = files.new_part()
            os.write(fd, b"replacement")
            os.close(fd)
            (directory / files.name(".blob")).write_bytes(b"original")
            with pytest.raises(FileExistsError):
                files.publish()
            assert (directory / files.name(".blob")).read_bytes() == b"original"
        else:
            suffix = ".part" if attack in {"part_link", "hardlink", "fifo"} else ".blob" if attack == "blob_link" else ".ready.json"
            path = directory / files.name(suffix)
            if attack == "hardlink":
                os.link(outside, path)
            elif attack == "fifo":
                os.mkfifo(path, 0o600)
            else:
                path.symlink_to(outside)
            with pytest.raises((CreatorError, OSError)):
                files.read(suffix, 2048)
    assert outside.read_bytes() == b"original"


@pytest.mark.parametrize("kind,count", [(".part", 15), (".ready.json", 64)])
def test_directory_capacity_counts_retained_partial_reservations_and_entries(tmp_path, kind, count):
    with io.PrivateMediaFiles(tmp_path.resolve(), "e" * 32) as files:
        directory = tmp_path / "media-persistence"
        for index in range(count):
            path = directory / (f"{index:032x}" + kind)
            path.write_bytes(b"x")
            path.chmod(0o600)
        with pytest.raises(CreatorError) as error:
            files.new_part()
        assert error.value.code == "MEDIA_STORAGE_CAPACITY"
        assert not files.exists(".part")


def test_directory_capacity_lock_serializes_independent_receptions(tmp_path):
    with io.PrivateMediaFiles(tmp_path.resolve(), "e" * 32):
        with pytest.raises(BlockingIOError):
            with io.PrivateMediaFiles(tmp_path.resolve(), "f" * 32):
                pytest.fail("concurrent capacity reservation")


def test_worker_imports_from_frozen_module_directory_when_caller_cwd_differs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with io.PrivateMediaFiles(tmp_path.resolve(), "f" * 32) as files:
        fd = files.new_part()
        try:
            # Rejected before DNS: this tests a real subprocess import/IPC, not
            # external transport or the local connector seam above.
            result = io.launch_download("http://unsafe.invalid/x", fd, seconds=1)
        finally:
            os.close(fd)
        assert result["ok"] is False and result["error"] == "NETWORK_OR_WORKER"


def test_pinned_connector_uses_checked_address_once_and_tls_hostname_verification(monkeypatch):
    events = []
    class Connection:
        def settimeout(self, value):
            events.append(("timeout", value))
        def connect(self, address):
            events.append(("connect", address))
        def close(self):
            events.append(("closed",))
    connection = Connection()
    monkeypatch.setattr(socket, "socket", lambda *a: connection)
    def wrapped(context, sock, *, server_hostname):
        assert context.check_hostname is True and context.verify_mode == io.ssl.CERT_REQUIRED
        assert server_hostname == "media.example.com" and sock is connection
        return connection
    monkeypatch.setattr(io.ssl.SSLContext, "wrap_socket", wrapped)
    addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]
    assert io.connect_pinned("media.example.com", addresses, 1) is connection
    assert events == [("timeout", 1), ("connect", ("8.8.8.8", 443))]


@pytest.mark.skipif(
    os.environ.get("D47_ISOLATED_RUNNER") != "1"
    or not os.environ.get("D47_SOCKET_LOG")
    or not os.environ.get("D47_LOCAL_LOG"),
    reason="Requires the isolated runner and inherited child network/fsync guard (D47_ISOLATED_RUNNER=1)",
)
def test_worker_independent_cutoff_rejects_complete_JSON_before_proof_fsync(tmp_path, monkeypatch):
    request = make_query()
    raw = raw_for(request, {"code": 0, "data": {"id": request.frozen()["accepted_task_id"], "status": "completed",
        "result": {"images": [{"url": "https://media.example.com/image.png"}]}}})
    query = {"descriptor": request.frozen(), "raw": raw}
    receipt = decode_query(request, raw)
    record = {"id": "a" * 32, "query_hash": digest(query), "receipt_hash": receipt["receipt_hash"], "url_sha256": receipt["result_url_sha256"],
        "created_at": (now() - timedelta(seconds=60)).isoformat(), "deadline_at": (now() + timedelta(seconds=120)).isoformat()}
    with local_http(monkeypatch) as (calls, _):
        with io.PrivateMediaFiles(tmp_path.resolve(), "a" * 32) as files:
            fd, proof, confirmation = files.new_part(), files.new_proof_fd(), files.new_confirmation_fd()
            monkeypatch.setenv("D47_DELAY_FSYNC_FD", str(proof))
            started = time.monotonic()
            try:
                result = io.launch_receive(query, record, fd, proof, confirmation, started_at=(now() - timedelta(seconds=43)).isoformat())
            finally:
                os.close(fd)
                os.close(proof)
                os.close(confirmation)
            assert result["ok"] is False and result["error"] == "TIMEOUT"
            assert time.monotonic() - started < 6 and len(calls) == 1
            # Data and complete proof JSON exist, but durable confirmation was
            # never produced after fsync. Recovery must require that marker.
            assert files.read(".part", io.POLICY["max_bytes"])
            assert files.read(".ready.json", 16384).endswith(b"}")
            with pytest.raises(CreatorError):
                files.read(".commit.json", 2048)
