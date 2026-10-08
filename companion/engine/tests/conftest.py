"""Engine tests run offline: any attempt to open a network connection fails the test."""
from __future__ import annotations

import socket

import pytest


class NetworkUsed(AssertionError):
    pass


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Record and refuse every DNS lookup and socket connection made during a test."""
    attempts: list[str] = []

    def refuse(name):
        def blocked(*args, **kwargs):
            attempts.append(name)
            raise NetworkUsed(f"a test tried the network ({name})")
        return blocked

    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
        monkeypatch.setattr(socket, name, refuse(name))
    monkeypatch.setattr(socket.socket, "connect", refuse("connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", refuse("connect_ex"))
    return attempts
