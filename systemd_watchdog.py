"""Minimal systemd watchdog notification support without extra dependencies."""

import os
import socket


def notify_watchdog() -> bool:
    """Tell systemd that the growbox automation loop completed successfully."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]

    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify_socket:
        notify_socket.connect(address)
        notify_socket.sendall(b"WATCHDOG=1")
    return True
