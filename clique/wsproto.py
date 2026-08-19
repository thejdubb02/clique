"""A minimal WebSocket server, because the terminal needs one and a framework
would be a heavier dependency than the protocol.

Only what a terminal actually uses: the handshake, text and binary frames,
fragmentation, ping/pong, close. No extensions, no permessage-deflate — tmux
output is already small and compressing it would cost CPU on a box that is
short of it, not bandwidth on a tailnet.

Framing rules that bite if forgotten: every client frame is masked and every
server frame must not be. Sending a masked frame from the server closes the
connection with no useful error in any browser.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import os
import select
import socket
import struct
import threading
import time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BINARY = 0x0, 0x1, 0x2
OP_CLOSE, OP_PING, OP_PONG = 0x8, 0x9, 0xA

#: A single frame larger than this is a bug or an attack, not a terminal.
MAX_PAYLOAD = 8 * 1024 * 1024

#: And a cap on the *reassembled* message, because the per-frame limit does not
#: bound anything on its own: a client can send continuation frames of 8 MB
#: each, forever, and a buffer that only stops growing at FIN grows until the
#: process dies. Keystrokes and resize messages are bytes; this is generous.
MAX_MESSAGE = 16 * 1024 * 1024

#: Seconds a half-delivered frame may stay half-delivered. Only ever applies
#: once a frame has begun; an idle connection is never on a clock.
FRAME_TIMEOUT = 30


class WebSocketError(Exception):
    pass


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + GUID).encode()).digest()  # noqa: S324 — RFC 6455 mandates SHA-1 here; it is a handshake tag, not a secret
    return base64.b64encode(digest).decode()


def handshake_response(client_key: str) -> bytes:
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key(client_key)}\r\n"
        "\r\n"
    ).encode()


class WebSocket:
    """One connection. Reads are single-threaded; writes are locked.

    Two threads legitimately write here — the PTY pump and the keepalive ping —
    so `send` serialises. Interleaved frames from unsynchronised writers are the
    classic way to corrupt a stream that then fails far from its cause.
    """

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self._send_lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------ send

    def send(self, payload: bytes, opcode: int = OP_BINARY) -> None:
        if self._closed:
            return
        header = bytearray()
        header.append(0x80 | opcode)  # FIN set: we never fragment outbound
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < (1 << 16):
            header.append(126)
            header += struct.pack("!H", length)
        else:
            header.append(127)
            header += struct.pack("!Q", length)
        with self._send_lock:
            if self._closed:
                return
            try:
                self.sock.sendall(bytes(header) + payload)
            except OSError:
                self._closed = True

    def send_text(self, text: str) -> None:
        self.send(text.encode(), OP_TEXT)

    def ping(self) -> None:
        self.send(b"", OP_PING)

    def close(self, code: int = 1000) -> None:
        if not self._closed:
            try:
                self.send(struct.pack("!H", code), OP_CLOSE)
            finally:
                self._closed = True
                with contextlib.suppress(OSError):
                    self.sock.shutdown(socket.SHUT_RDWR)

    @property
    def closed(self) -> bool:
        return self._closed

    # ------------------------------------------------------------------ recv

    def _read_exact(self, count: int) -> bytes:
        """Exactly `count` bytes, with a deadline once a frame has started.

        Waiting for the *first* byte is normal and must not time out: a
        terminal can sit idle for hours and the thread parked on it is doing
        its job. A socket that has delivered half a header and then stopped is
        a different thing — it is broken, or it is holding a thread open on
        purpose, and each held thread has a PTY and a tmux viewer behind it.

        The deadline is enforced with `select` rather than a socket timeout on
        purpose. A timeout is a property of the socket, not of the read, so it
        would apply to `sendall` too — and a `sendall` that gives up halfway
        leaves the peer holding half a frame, which is a corrupted stream
        rather than a closed one.
        """
        chunks = bytearray()
        deadline: float | None = None
        while len(chunks) < count:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self.sock], [], [], remaining)[0]:
                    raise WebSocketError("frame stalled")
            chunk = self.sock.recv(count - len(chunks))
            if not chunk:
                raise WebSocketError("connection closed mid-frame")
            chunks += chunk
            if deadline is None and len(chunks) < count:
                deadline = time.monotonic() + FRAME_TIMEOUT
        return bytes(chunks)

    def recv(self) -> tuple[int, bytes] | None:
        """Next application message, or None once the peer has gone.

        Control frames are answered here and never surface to the caller —
        a ping that waits on application code is a ping that times out.
        """
        buffer = bytearray()
        message_op = None

        while True:
            try:
                first, second = self._read_exact(2)
            except (WebSocketError, OSError):
                return None

            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F

            # RFC 6455 §5.1: every frame from a client is masked, and a server
            # that receives an unmasked one MUST fail the connection. Masking
            # is not confidentiality — it exists so a hostile page cannot make
            # a proxy see attacker-chosen bytes as a second request. Accepting
            # unmasked frames is how a browser-facing socket becomes a way to
            # poison a cache in front of it.
            if not masked:
                self.close(1002)
                return None

            try:
                if length == 126:
                    length = struct.unpack("!H", self._read_exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", self._read_exact(8))[0]
                if length > MAX_PAYLOAD:
                    self.close(1009)
                    return None
                mask = self._read_exact(4) if masked else b""
                payload = self._read_exact(length) if length else b""
            except (WebSocketError, OSError):
                return None

            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

            # RFC 6455 §5.5: a control frame carries at most 125 bytes and is
            # never fragmented. Without this a caller with no write permission
            # — which may open a socket in order to watch — could send 8 MB
            # pings all day and have each one buffered and echoed back.
            if opcode >= OP_CLOSE and (length > 125 or not fin):
                self.close(1002)
                return None

            if opcode == OP_CLOSE:
                self.close()
                return None
            if opcode == OP_PING:
                self.send(payload, OP_PONG)
                continue
            if opcode == OP_PONG:
                continue

            if opcode in (OP_TEXT, OP_BINARY):
                message_op = opcode
            # Checked before appending, not after: appending first means the
            # buffer legitimately holds MAX_MESSAGE plus one whole frame at
            # the moment it is rejected, and that overshoot is 8 MB.
            if len(buffer) + len(payload) > MAX_MESSAGE:
                self.close(1009)
                return None
            buffer += payload
            if fin:
                return (message_op or OP_BINARY, bytes(buffer))


def mask_free_random(length: int) -> bytes:
    """Only used by the test client — servers never mask."""
    return os.urandom(length)
