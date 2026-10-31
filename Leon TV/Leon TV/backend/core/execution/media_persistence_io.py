"""Bounded direct HTTPS and private, no-overwrite image transport files."""
import fcntl
import hashlib
import http.client
import ipaddress
import json
import os
import re
import signal
import socket
import ssl
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from core.creator.store import CreatorError, utc_now
from core.execution.media_persistence_schema import POLICY

_WORKER_CUTOFF = None


def timed_out(*args):
    raise TimeoutError("Bounded media worker deadline reached")


@contextmanager
def worker_deadline(job):
    """Independent of the parent: includes DNS, TLS, decoding and proof fsync."""
    from core.execution.image_policy import timestamp
    from core.execution.policy import now

    global _WORKER_CUTOFF
    seconds = job["seconds"]
    require(type(seconds) in (int, float) and 0 < seconds <= POLICY["absolute_timeout_seconds"])
    current = now()
    remaining = seconds
    if "started_at" in job:
        elapsed = (current - timestamp(job["started_at"])).total_seconds()
        require(elapsed >= 0)
        remaining = min(remaining, seconds - elapsed)
    if job.get("receive") is True:
        remaining = min(remaining, (timestamp(job["record"]["deadline_at"]) - current).total_seconds() - POLICY["cleanup_seconds"])
    if remaining <= 0:
        timed_out()
    previous = signal.signal(signal.SIGALRM, timed_out)
    _WORKER_CUTOFF = time.monotonic() + remaining
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _WORKER_CUTOFF = None


def require(value, code="MEDIA_RECEIVE_IO"):
    if not value:
        raise CreatorError(code, "Private bounded media transfer failed", 409)


def target(url):
    require(type(url) is str and 0 < len(url) <= 8192 and url.isascii()
            and not any(ord(c) < 33 or ord(c) == 127 for c in url) and "\\" not in url)
    parsed = urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname is not None and parsed.port in (None, 443)
            and parsed.username is None and parsed.password is None and not parsed.fragment)
    host = parsed.hostname
    require(host == host.lower() and not host.endswith(".") and "%" not in host
            and re.fullmatch(r"[a-z0-9.-]{1,253}", host) and ".." not in host
            and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")))
    # DNS names only. Literal addresses cannot opt around target policy.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        require(False)
    return host, parsed.path or "/", parsed.query


def public_addresses(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    require(0 < len(addresses) <= 32)
    require(all(ipaddress.ip_address(item[4][0]).is_global
                and not ipaddress.ip_address(item[4][0]).is_multicast
                and not ipaddress.ip_address(item[4][0]).is_unspecified
                and not ipaddress.ip_address(item[4][0]).is_reserved for item in addresses))
    return addresses


def connect_pinned(host, addresses, timeout):
    # One checked address, no DNS re-resolution or alternate connection retry.
    family, kind, protocol, _, address = addresses[0]
    connection = socket.socket(family, kind, protocol)
    try:
        connection.settimeout(timeout)
        connection.connect(address)
        return ssl.create_default_context().wrap_socket(connection, server_hostname=host)
    except BaseException:
        connection.close()
        raise


def download_to_fd(url, output_fd, *, seconds, maximum):
    """One request. Parent process supplies the hard wall-clock bound too."""
    require(type(maximum) is int and 0 < maximum <= POLICY["max_bytes"]
            and type(seconds) in (int, float) and 0 < seconds <= POLICY["absolute_timeout_seconds"])
    host, path, query = target(url)
    until = time.monotonic() + seconds
    if _WORKER_CUTOFF is not None:
        until = min(until, _WORKER_CUTOFF)
    addresses = public_addresses(host)
    remaining = until - time.monotonic()
    require(remaining > 0, "MEDIA_RECEIVE_TIMEOUT")
    connection = http.client.HTTPSConnection(host, timeout=min(POLICY["network_timeout_seconds"], remaining))
    try:
        connection.sock = connect_pinned(host, addresses, min(POLICY["network_timeout_seconds"], remaining))
        require(time.monotonic() < until, "MEDIA_RECEIVE_TIMEOUT")
        connection.request("GET", path + ("?" + query if query else ""), headers={"Accept": "image/png,image/jpeg,image/webp", "Connection": "close"})
        response = connection.getresponse()
        if response.status != 200:
            return {"ok": False, "http_status": response.status, "error": "HTTP_NOT_200"}
        length = response.getheader("Content-Length")
        if length is not None:
            require(re.fullmatch(r"[0-9]{1,12}", length) is not None)
            require(0 < int(length) <= maximum, "MEDIA_RESOURCE_LIMIT")
        require(response.getheader("Content-Encoding", "identity").lower() == "identity")
        content_type = response.getheader("Content-Type")
        if content_type is not None:
            content_type = content_type.split(";", 1)[0].strip().lower()
            require(content_type in {"image/png", "image/jpeg", "image/webp", "application/octet-stream"}, "MEDIA_BYTES_INVALID")
        size = 0
        hashed = hashlib.sha256()
        while True:
            remaining = until - time.monotonic()
            require(remaining > 0, "MEDIA_RECEIVE_TIMEOUT")
            if connection.sock is not None:
                connection.sock.settimeout(min(POLICY["network_timeout_seconds"], remaining))
            chunk = response.read(min(65536, maximum + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            require(size <= maximum, "MEDIA_RESOURCE_LIMIT")
            hashed.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(output_fd, view)
                require(written > 0)
                view = view[written:]
        require(0 < size <= maximum and (length is None or size == int(length)) and time.monotonic() <= until)
        os.fsync(output_fd)
        return {"ok": True, "http_status": 200, "sha256": hashed.hexdigest(), "byte_length": size, "content_type": content_type}
    finally:
        connection.close()


def launch_download(url, output_fd, *, seconds):
    return launch_job({"url": url, "output_fd": output_fd, "seconds": seconds, "maximum": POLICY["max_bytes"]}, (output_fd,), seconds)


def launch_job(job, file_descriptors, seconds):
    """A fresh process bounds DNS, TLS, headers and stream together; no Key/env proxies."""
    env = {k: v for k, v in os.environ.items() if not any(s in k.lower() for s in ("proxy", "api_key", "token", "password", "secret"))}
    request = json.dumps(job).encode()
    require(len(request) <= 16 * 1048576)
    process = subprocess.Popen([sys.executable, "-B", "-m", "core.execution.media_persistence_io"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               pass_fds=file_descriptors, env=env, start_new_session=True,
                               cwd=Path(__file__).resolve().parents[2])
    try:
        out, _ = process.communicate(request, timeout=seconds)
        require(process.returncode == 0 and len(out) <= 2048)
        result = json.loads(out)
        require(type(result) is dict and type(result.get("ok")) is bool)
        return result
    except BaseException:
        process.kill()
        process.wait(timeout=POLICY["cleanup_seconds"])
        raise


def launch_receive(query, record, output_fd, proof_fd, confirmation_fd, *, started_at):
    from core.execution.media_persistence import intent_hash

    job = {"receive": True, "query": query, "record": record, "intent_hash": intent_hash(record),
           "output_fd": output_fd, "proof_fd": proof_fd, "confirmation_fd": confirmation_fd, "started_at": started_at,
           "seconds": POLICY["absolute_timeout_seconds"], "maximum": POLICY["max_bytes"]}
    return launch_job(job, (output_fd, proof_fd, confirmation_fd), POLICY["absolute_timeout_seconds"])


def receive_job(job):
    """Transport worker can write only its already opened private byte/proof FDs."""
    from core.creator.store import digest, utc_now
    from core.execution.image_policy import FrozenImageRequest, timestamp
    from core.execution.image_receipts import decode_query, verify_delivery

    query, record = job["query"], job["record"]
    request = FrozenImageRequest.from_frozen(query["descriptor"])
    receipt = decode_query(request, query["raw"])
    require(receipt["classification"] == "completed" and digest(query) == record["query_hash"]
            and receipt["receipt_hash"] == record["receipt_hash"] and receipt["result_url_sha256"] == record["url_sha256"])
    result = download_to_fd(receipt["result_url"], job["output_fd"], seconds=job["seconds"], maximum=job["maximum"])
    if not result["ok"]:
        return result
    try:
        os.lseek(job["output_fd"], 0, os.SEEK_SET)
        image = bytearray()
        while len(image) <= POLICY["max_bytes"]:
            chunk = os.read(job["output_fd"], min(65536, POLICY["max_bytes"] + 1 - len(image)))
            if not chunk:
                break
            image.extend(chunk)
        require(0 < len(image) <= POLICY["max_bytes"])
        image = bytes(image)
        artifact = verify_delivery(request, query["raw"], image)
        require(result["content_type"] in {None, "application/octet-stream", {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[artifact["format"]]})
        saved_at = utc_now()
        require(result["sha256"] == artifact["delivery_sha256"] and result["byte_length"] == artifact["delivery_size_bytes"]
                and timestamp(record["created_at"]) <= timestamp(job["started_at"]) <= timestamp(saved_at) <= timestamp(record["deadline_at"])
                and (timestamp(saved_at) - timestamp(job["started_at"])).total_seconds() <= POLICY["absolute_timeout_seconds"])
        saved = {"sha256": artifact["delivery_sha256"], "byte_length": artifact["delivery_size_bytes"], "artifact": artifact,
                 "saved_at": saved_at, "network_started_at": job["started_at"]}
        proof = {"intent_hash": job["intent_hash"], "query_hash": record["query_hash"], "receipt_hash": record["receipt_hash"], "saved": saved}
        raw = json.dumps(proof, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
        require(len(raw) <= 16384)
        view = memoryview(raw)
        while view:
            written = os.write(job["proof_fd"], view)
            require(written > 0)
            view = view[written:]
        os.fsync(job["proof_fd"])
        # This timestamp is taken AFTER data+proof fsync, never before their
        # write. A complete early JSON alone is insufficient recovery evidence.
        confirmed_at = utc_now()
        require(timestamp(confirmed_at) <= timestamp(record["deadline_at"])
                and (timestamp(confirmed_at) - timestamp(job["started_at"])).total_seconds() <= POLICY["absolute_timeout_seconds"])
        confirmation = {"intent_hash": job["intent_hash"], "proof_sha256": hashlib.sha256(raw).hexdigest(), "confirmed_at": confirmed_at}
        confirmation["confirmation_hash"] = digest(confirmation)
        confirmation_raw = json.dumps(confirmation, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
        require(len(confirmation_raw) <= 2048)
        view = memoryview(confirmation_raw)
        while view:
            written = os.write(job["confirmation_fd"], view)
            require(written > 0)
            view = view[written:]
        os.fsync(job["confirmation_fd"])
        return result
    except TimeoutError:
        raise
    except Exception:
        return {"ok": False, "http_status": 200, "error": "INVALID_BYTES"}


class PrivateMediaFiles:
    """Only fixed names below one opened private directory; no arbitrary paths."""
    def __init__(self, root, identifier, *, read_only=False):
        require(type(read_only) is bool)
        self.read_only = read_only
        require(type(identifier) is str and re.fullmatch(r"[a-f0-9]{32}", identifier))
        root = Path(root)
        require(root.is_absolute() and root.resolve() == root)
        current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for name in root.parts[1:]:
                next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                os.close(current)
                current = next_fd
            if not read_only:
                try:
                    os.mkdir("media-persistence", 0o700, dir_fd=current)
                except FileExistsError:
                    pass
            self.fd = os.open("media-persistence", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
        finally:
            os.close(current)
        info = os.fstat(self.fd)
        if not (info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700):
            os.close(self.fd)
            require(False)
        self.identifier = identifier
        self.capacity_fd = None

    def __enter__(self):
        try:
            flags = os.O_RDONLY | os.O_NOFOLLOW if self.read_only else os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
            self.capacity_fd = os.open(".capacity.lock", flags, 0o600, dir_fd=self.fd)
            info = os.fstat(self.capacity_fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600)
            fcntl.flock(self.capacity_fd, (fcntl.LOCK_SH if self.read_only else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BaseException:
            if self.capacity_fd is not None:
                os.close(self.capacity_fd)
            os.close(self.fd)
            raise
        return self

    def __exit__(self, *args):
        fcntl.flock(self.capacity_fd, fcntl.LOCK_UN)
        os.close(self.capacity_fd)
        os.close(self.fd)

    def name(self, suffix):
        require(suffix in {".part", ".blob", ".ready.json", ".commit.json"})
        return self.identifier + suffix

    def exists(self, suffix):
        try:
            os.stat(self.name(suffix), dir_fd=self.fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def new_part(self):
        require(not self.read_only)
        require(self.capacity_fd is not None)
        size, slots = 0, 0
        # A partial transfer reserves its full data+proof allowance. Failed
        # partials retain that reservation until an explicit local cleanup.
        for name in os.listdir(self.fd):
            if name == ".capacity.lock":
                continue
            require(re.fullmatch(r"[a-f0-9]{32}(?:\.part|\.blob|\.ready\.json|\.commit\.json)", name))
            info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600)
            require(info.st_size <= (16384 if name.endswith(".ready.json") else 2048 if name.endswith(".commit.json") else POLICY["max_bytes"]))
            size += POLICY["max_bytes"] + 18432 if name.endswith(".part") else info.st_size
            slots += 4 if name.endswith(".part") else 1
        require(size + POLICY["max_bytes"] + 18432 <= POLICY["max_retained_bytes"]
                and slots + 4 <= POLICY["max_retained_entries"], "MEDIA_STORAGE_CAPACITY")
        require(not self.exists(".blob") and not self.exists(".ready.json") and not self.exists(".commit.json"))
        return os.open(self.name(".part"), os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)

    def read(self, suffix, maximum, *, paired_link=False):
        fd = os.open(self.name(suffix), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == (2 if paired_link else 1)
                    and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= maximum)
            result = bytearray()
            while len(result) <= maximum:
                chunk = os.read(fd, min(65536, maximum + 1 - len(result)))
                if not chunk:
                    break
                result.extend(chunk)
            require(0 < len(result) <= maximum and len(result) == info.st_size)
            return bytes(result)
        finally:
            os.close(fd)

    def read_payload(self, maximum):
        if self.exists(".part") and self.exists(".blob"):
            a = os.stat(self.name(".part"), dir_fd=self.fd, follow_symlinks=False)
            b = os.stat(self.name(".blob"), dir_fd=self.fd, follow_symlinks=False)
            require(stat.S_ISREG(a.st_mode) and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) and a.st_nlink == 2)
            return self.read(".blob", maximum, paired_link=True), ".blob"
        suffix = ".blob" if self.exists(".blob") else ".part"
        return self.read(suffix, maximum), suffix

    def new_proof_fd(self):
        require(not self.read_only)
        return os.open(self.name(".ready.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)

    def new_confirmation_fd(self):
        require(not self.read_only)
        return os.open(self.name(".commit.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)

    def proof(self, value):
        require(not self.read_only)
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
        require(len(raw) <= 16384)
        fd = os.open(self.name(".ready.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        finally:
            os.fsync(self.fd)

    def publish(self):
        require(not self.read_only)
        # Hard-link publication is atomic and cannot overwrite an existing file.
        os.link(self.name(".part"), self.name(".blob"), src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
        os.fsync(self.fd)
        os.unlink(self.name(".part"), dir_fd=self.fd)
        os.fsync(self.fd)

    def finish_link(self):
        require(not self.read_only)
        # Crash exactly between link and unlink: only these two fixed names may
        # refer to the same private inode, and the completed proof is still needed.
        if self.exists(".part") and self.exists(".blob"):
            a = os.stat(self.name(".part"), dir_fd=self.fd, follow_symlinks=False)
            b = os.stat(self.name(".blob"), dir_fd=self.fd, follow_symlinks=False)
            require(stat.S_ISREG(a.st_mode) and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) and a.st_nlink == 2)
            os.unlink(self.name(".part"), dir_fd=self.fd)
            os.fsync(self.fd)


if __name__ == "__main__":
    try:
        entered_at = utc_now()
        signal.signal(signal.SIGALRM, timed_out)
        signal.setitimer(signal.ITIMER_REAL, POLICY["absolute_timeout_seconds"])
        raw = sys.stdin.buffer.read(16 * 1048576 + 1)
        require(len(raw) <= 16 * 1048576)
        job = json.loads(raw)
        job.setdefault("started_at", entered_at)
        with worker_deadline(job):
            result = receive_job(job) if job.get("receive") is True else download_to_fd(job["url"], job["output_fd"], seconds=job["seconds"], maximum=job["maximum"])
        print(json.dumps(result))
    except BaseException as exc:
        code = "TIMEOUT" if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else "TRUNCATED_RESPONSE" if isinstance(exc, http.client.IncompleteRead) else "RESOURCE_LIMIT" if getattr(exc, "code", None) == "MEDIA_RESOURCE_LIMIT" else "INVALID_BYTES" if getattr(exc, "code", None) == "MEDIA_BYTES_INVALID" else "NETWORK_OR_WORKER"
        print(json.dumps({"ok": False, "http_status": None, "error": code}))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
