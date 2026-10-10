"""Validation subprocess guard. No network access, including Telegram."""
import socket
def _deny(*args,**kwargs):
    raise RuntimeError('E1 validation forbids outbound network/Telegram')
socket.socket.connect=_deny
socket.socket.connect_ex=_deny
socket.socket.sendto=_deny
socket.create_connection=_deny
