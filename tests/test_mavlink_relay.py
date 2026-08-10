"""The relay is in the live control path, so prove it moves bytes exactly.

``scripts/mavlink_relay.py`` sits between the MCP server and the autopilot and
copies everything to the wire tap. Everything else in the capture layer is
passive; this one is not. The question these tests answer is the one that
matters for flight safety rather than for data: can the pump lose, reorder or
corrupt a byte *going to the vehicle*?

They drive the real ``Relay`` over real sockets, with the payload deliberately
written in awkward fragments so that TCP segment boundaries fall inside
"messages", and check the upstream received the exact byte stream. They also
pin what the mirror does and does not promise: it is a copy of both directions
interleaved into one UDP stream, which is a log, not a framing.
"""

import importlib.util
import socket
import threading
import time
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mavlink_relay.py"
_spec = importlib.util.spec_from_file_location("mavlink_relay", _SCRIPT)
relay_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(relay_mod)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Upstream:
    """A one-connection TCP server standing in for MAVProxy/SITL."""

    def __init__(self, reply: bytes = b""):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received = bytearray()
        self.reply = reply
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        conn, _ = self.sock.accept()
        with conn:
            if self.reply:
                conn.sendall(self.reply)
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                self.received.extend(chunk)

    def close(self):
        self.thread.join(timeout=5.0)
        self.sock.close()


def _mavlink_ish(count: int) -> bytes:
    """``count`` MAVLink v2-shaped frames, each a different length."""
    out = bytearray()
    for i in range(count):
        payload = bytes((i + j) % 256 for j in range(1 + i % 37))
        out += bytes([0xFD, len(payload), 0, 0, i % 256, 1, 1, i % 256, 0, 0]) + payload + b"\xaa\xbb"
    return bytes(out)


def _run_relay(upstream_port: int, mirror_port: int):
    listen_port = _free_port()
    relay = relay_mod.Relay(
        listen=("127.0.0.1", listen_port),
        upstream=("127.0.0.1", upstream_port),
        mirror=relay_mod.Mirror(("127.0.0.1", mirror_port)),
    )
    thread = threading.Thread(target=relay.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)  # let it bind before the client dials in
    return relay, listen_port, thread


def test_every_byte_to_the_vehicle_arrives_intact_and_in_order():
    """Fragmented writes must not become fragmented commands.

    The pump reads whatever TCP hands it, so frames routinely straddle two
    reads. What the autopilot must see is the same byte stream the server sent,
    unchanged - the relay is explicitly a byte pump and must never re-frame.
    """
    payload = _mavlink_ish(400)
    upstream = _Upstream()
    mirror = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mirror.bind(("127.0.0.1", 0))
    relay, listen_port, thread = _run_relay(upstream.port, mirror.getsockname()[1])

    client = socket.create_connection(("127.0.0.1", listen_port), timeout=5.0)
    # Awkward fragment sizes, so segment boundaries land mid-frame.
    step, offset = 7, 0
    while offset < len(payload):
        client.sendall(payload[offset : offset + step])
        offset += step
        step = 1 + (step * 3) % 293
    client.close()
    upstream.close()
    relay._stop.set()

    assert bytes(upstream.received) == payload
    mirror.close()


def test_the_vehicles_replies_reach_the_server_intact():
    downstream = _mavlink_ish(200)
    upstream = _Upstream(reply=downstream)
    mirror = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mirror.bind(("127.0.0.1", 0))
    relay, listen_port, thread = _run_relay(upstream.port, mirror.getsockname()[1])

    client = socket.create_connection(("127.0.0.1", listen_port), timeout=5.0)
    client.settimeout(5.0)
    got = bytearray()
    while len(got) < len(downstream):
        chunk = client.recv(4096)
        if not chunk:
            break
        got.extend(chunk)
    client.close()
    upstream.close()
    relay._stop.set()

    assert bytes(got) == downstream
    mirror.close()


def test_a_dead_mirror_never_costs_the_flight_link_a_byte():
    """The tap is observability. It must not be able to break the control path.

    Mirror.send swallows its errors and counts them; this checks the pump keeps
    delivering after the mirror socket has been closed under it.
    """
    payload = _mavlink_ish(50)
    upstream = _Upstream()
    relay, listen_port, thread = _run_relay(upstream.port, _free_port())
    relay.mirror.close()  # as if the UDP socket had gone away mid-session

    client = socket.create_connection(("127.0.0.1", listen_port), timeout=5.0)
    client.sendall(payload)
    client.close()
    upstream.close()
    relay._stop.set()

    assert bytes(upstream.received) == payload


def test_the_mirror_is_a_copy_of_both_directions_not_a_framing():
    """What the tap is handed, stated plainly so a reader of the tlog knows.

    Both directions are copied into ONE UDP stream, in whatever chunks the
    pumps happened to read, so a datagram boundary is not a message boundary
    and the two directions interleave. pymavlink's parser carries bytes across
    datagrams, which is why this works at all - but a lost or reordered
    datagram costs the tap whatever frames straddled it, and nothing in the
    tlog says so.
    """
    mirror = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mirror.bind(("127.0.0.1", 0))
    mirror.settimeout(2.0)
    downstream = b"\xfd" + bytes(range(64))
    upstream = _Upstream(reply=downstream)
    relay, listen_port, thread = _run_relay(upstream.port, mirror.getsockname()[1])

    client = socket.create_connection(("127.0.0.1", listen_port), timeout=5.0)
    client.settimeout(5.0)
    client.sendall(b"\xfd" + bytes(range(32)))
    time.sleep(0.3)
    client.close()
    upstream.close()
    relay._stop.set()

    seen = []
    try:
        while True:
            seen.append(mirror.recv(65535))
    except socket.timeout:
        pass
    mirror.close()

    # Both halves of the link are present, in the same stream.
    assert b"".join(seen) == b"\xfd" + bytes(range(64)) + b"\xfd" + bytes(range(32)) or b"".join(
        seen
    ) == b"\xfd" + bytes(range(32)) + b"\xfd" + bytes(range(64))
    assert relay.mirror.bytes_sent == sum(len(d) for d in seen)
