#!/usr/bin/env python3
"""A MAVLink relay that gives the wire tap both directions of the link.

Why this exists
---------------
Plan 19 requires the per-trial ``mavlink.tlog`` to contain **everything on the
wire, both ways** - the commands the MCP server sends as well as the telemetry
the vehicle returns. The obvious way to feed
:class:`droneserver.capture.MavlinkTap` is a MAVProxy ``--out udpout:...``
forward from the SITL box, and that is what was tried first. It does not work,
and the reason is structural rather than a misconfiguration:

    MAVProxy forwards **master -> outputs** and **output -> master**. It never
    forwards **output -> output**. A client that connects on one output
    (``tcpin:...:5678``, which is how the MCP server attaches) has its commands
    routed to the autopilot and to nothing else. A tap sitting on a *different*
    output therefore hears the vehicle and only the vehicle.

Measured, not assumed: a 6-second tap on the SITL's ``udpout`` forward during
three tool calls recorded 763 messages, **every one of them from sysid 1**, and
not a single ``COMMAND_LONG`` from the server. (See ``process_mavlink()`` in
MAVProxy's ``mavproxy.py``: slave traffic goes to ``mpstate.master()`` and the
log queue, never to ``mav_outputs``.)

So the copy has to be made at a point that sees both halves. This relay is that
point - the ``mavlink-router``-shaped element the capture design assumed was
there:

    MCP server (MAVSDK)  --TCP-->  [ relay ]  --TCP-->  MAVProxy / SITL
                                       |
                                       +--UDP--> wire tap (mavlink.tlog)

It is a byte pump. It does not parse, rewrite, reorder, filter or synthesise
MAVLink; it forwards each chunk verbatim and sends a copy of the same bytes to
the mirror address. The tap's existing sysid-based direction heuristic then
labels them, exactly as documented in :mod:`droneserver.capture.mavlink_tap`.

Usage::

    python scripts/mavlink_relay.py \\
        --listen 127.0.0.1:5679 \\
        --upstream 100.80.7.20:6789 \\
        --mirror 127.0.0.1:14650

then point the server at the relay (``MAVLINK_ADDRESS=127.0.0.1``,
``MAVLINK_PORT=5679``) and the tap at ``udpin:127.0.0.1:14650``.

One client at a time, by design: MAVSDK opens exactly one TCP connection, and
the upstream (MAVProxy ``tcpin``) accepts one client too. When the client goes
away the upstream connection is closed with it and the relay returns to
accepting, so a server restart gets a clean link rather than a half-open one.
"""

import argparse
import socket
import sys
import threading
import time

#: Read size for each direction. Small enough that a mirrored chunk is always
#: one UDP datagram, large enough to never be the bottleneck.
CHUNK = 4096
#: Seconds to wait for the upstream (SITL) TCP connection.
CONNECT_TIMEOUT_S = 10.0


def _split_host_port(spec: str, default_host: str = "127.0.0.1") -> tuple[str, int]:
    """Parse ``host:port`` (or a bare ``port``) into a tuple."""
    spec = spec.strip()
    if ":" in spec:
        host, _, port = spec.rpartition(":")
        return (host or default_host), int(port)
    return default_host, int(spec)


class Mirror:
    """Sends a copy of every byte to a UDP address. Never raises at the caller."""

    def __init__(self, address: tuple[str, int] | None):
        self.address = address
        self.bytes_sent = 0
        self.errors = 0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if address else None

    def send(self, data: bytes) -> None:
        if self._sock is None or self.address is None:
            return
        try:
            self._sock.sendto(data, self.address)
            self.bytes_sent += len(data)
        except OSError:
            # A mirror that fails must never take the flight link down with it.
            self.errors += 1

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class Relay:
    """Accepts one MAVLink client at a time and bridges it to the upstream."""

    def __init__(self, listen: tuple[str, int], upstream: tuple[str, int], mirror: Mirror):
        self.listen = listen
        self.upstream = upstream
        self.mirror = mirror
        self.sessions = 0
        self.bytes_up = 0
        self.bytes_down = 0
        self._stop = threading.Event()

    def _log(self, message: str) -> None:
        print(f"[relay {time.strftime('%H:%M:%S')}] {message}", flush=True)

    def serve_forever(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(self.listen)
        server.listen(1)
        server.settimeout(1.0)
        self._log(
            f"listening on {self.listen[0]}:{self.listen[1]} -> "
            f"{self.upstream[0]}:{self.upstream[1]}, mirroring to {self.mirror.address}"
        )
        try:
            while not self._stop.is_set():
                try:
                    client, peer = server.accept()
                except socket.timeout:
                    continue
                self.sessions += 1
                self._log(f"client {peer[0]}:{peer[1]} connected (session {self.sessions})")
                self._session(client)
                self._log(
                    f"session ended; up={self.bytes_up} down={self.bytes_down} "
                    f"mirrored={self.mirror.bytes_sent} mirror_errors={self.mirror.errors}"
                )
        except KeyboardInterrupt:
            pass
        finally:
            server.close()
            self.mirror.close()

    def _session(self, client: socket.socket) -> None:
        try:
            up = socket.create_connection(self.upstream, timeout=CONNECT_TIMEOUT_S)
        except OSError as e:
            self._log(f"upstream connect failed ({e}); dropping client")
            client.close()
            return
        for sock in (client, up):
            sock.settimeout(None)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

        done = threading.Event()

        def pump(src: socket.socket, dst: socket.socket, upward: bool) -> None:
            try:
                while not done.is_set():
                    data = src.recv(CHUNK)
                    if not data:
                        break
                    dst.sendall(data)
                    self.mirror.send(data)
                    if upward:
                        self.bytes_up += len(data)
                    else:
                        self.bytes_down += len(data)
            except OSError:
                pass
            finally:
                done.set()

        threads = [
            threading.Thread(target=pump, args=(client, up, True), name="relay-up", daemon=True),
            threading.Thread(target=pump, args=(up, client, False), name="relay-down", daemon=True),
        ]
        for t in threads:
            t.start()
        done.wait()
        # Closing both ends unblocks whichever pump is still in recv().
        for sock in (client, up):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        for t in threads:
            t.join(timeout=2.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--listen", default="127.0.0.1:5679", help="host:port the MCP server connects to (default 127.0.0.1:5679)"
    )
    parser.add_argument("--upstream", required=True, help="host:port of the autopilot/MAVProxy TCP endpoint")
    parser.add_argument(
        "--mirror", default="127.0.0.1:14650", help="UDP host:port to copy both directions to (default 127.0.0.1:14650)"
    )
    args = parser.parse_args(argv)

    relay = Relay(
        listen=_split_host_port(args.listen),
        upstream=_split_host_port(args.upstream),
        mirror=Mirror(_split_host_port(args.mirror)),
    )
    relay.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
