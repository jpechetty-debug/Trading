"""
conftest.py — pytest configuration for Indian Stock AI V6.5.

Adds the project root to sys.path so all `src.*` imports resolve
cleanly. This is the ONLY place sys.path manipulation should exist.
"""
import sys
import os
import socket
import pytest

# Add project root to path for src.* imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


@pytest.fixture(autouse=True)
def block_live_network(monkeypatch):
    """Unit/integration tests must stub providers instead of contacting them."""
    def forbidden(*args, **kwargs):
        raise AssertionError('Live network access is disabled in the offline test suite')
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    def connect(sock, address):
        # Windows asyncio uses loopback socketpair for its internal wake-up pipe.
        if isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1'):
            return original_connect(sock, address)
        return forbidden()
    def connect_ex(sock, address):
        if isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1'):
            return original_connect_ex(sock, address)
        return forbidden()
    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', connect_ex)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
