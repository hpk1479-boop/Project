"""Revision48 test process: block all socket egress before importing project code."""
import socket

def _deny(*args, **kwargs):
    raise AssertionError('REV48_NETWORK_BLOCKED: real Telegram/network transmission is forbidden')
for name in ('connect', 'connect_ex', 'sendto'):
    setattr(socket.socket, name, _deny)
socket.create_connection = _deny
