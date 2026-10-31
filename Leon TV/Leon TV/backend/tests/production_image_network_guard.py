"""Public isolated runner guard: no external network; one explicit local seam."""
import os
import socket
import ssl

_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_dns = socket.getaddrinfo
_wrap = ssl.SSLContext.wrap_socket


def _log(name, value):
    with open(os.environ[name], "a") as stream:
        stream.write(value + "\n")


def connect(sock, address):
    port = os.environ.get("D48_LOOPBACK_PORT")
    if port and isinstance(address, tuple) and address[:2] == ("8.8.8.8", 443):
        _log("D48_LOCAL_LOG", "mapped_worker")
        return _connect(sock, ("127.0.0.1", int(port)))
    if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
        _log("D48_LOCAL_LOG", "loopback")
        return _connect(sock, address)
    _log("D48_EXTERNAL_LOG", "blocked_connect")
    raise RuntimeError("Isolated production-image external socket denied")


def connect_ex(sock, address):
    connect(sock, address)
    return 0


def dns(host, port, *args, **kwargs):
    if os.environ.get("D48_LOOPBACK_PORT") and host == "media.example.com" and port == 443:
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))]
    if host in {"127.0.0.1", "::1", "localhost"}:
        return _dns(host, port, *args, **kwargs)
    _log("D48_EXTERNAL_LOG", "blocked_dns")
    raise RuntimeError("Isolated production-image external DNS denied")


def tls(context, sock, *args, **kwargs):
    if os.environ.get("D48_LOOPBACK_PORT") and kwargs.get("server_hostname") == "media.example.com":
        return sock
    return _wrap(context, sock, *args, **kwargs)


socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
socket.getaddrinfo = dns
ssl.SSLContext.wrap_socket = tls
