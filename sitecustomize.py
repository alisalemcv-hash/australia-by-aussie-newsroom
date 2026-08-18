"""Prefer IPv4 on GitHub-hosted runners.

Some runners can resolve an external hostname to IPv6 while having no usable
IPv6 route. Requests/urllib3 may then fail with Errno 101 before trying IPv4.
Filtering DNS results to IPv4 keeps normal HTTPS requests working without
changing the application code or credentials.
"""
import socket

_original_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    results = _original_getaddrinfo(host, port, family, type, proto, flags)
    ipv4 = [item for item in results if item[0] == socket.AF_INET]
    return ipv4 or results


socket.getaddrinfo = _ipv4_only_getaddrinfo
