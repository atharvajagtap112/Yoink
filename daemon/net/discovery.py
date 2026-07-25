"""mDNS peer discovery over zeroconf.

Advertises this instance as a _yoink._tcp.local. service and browses for others
of the same type, reporting peers as they appear and disappear.

Two ids are in play and they are NOT the same thing:
  * the mDNS *instance label* (name + random suffix) is unique per launch, so
    two instances on one host don't collide in the service namespace;
  * the *device_id* (config.device_id, stable across launches) is what we report
    to the mesh, because pairing and the "lower id dials" rule must survive a
    restart. It rides along in the TXT record.

We advertise the LAN IP, not 127.0.0.1, because real peers (the phone) have to
be able to reach us. Loopback testing still works — two instances on this laptop
just connect via the laptop's own LAN IP instead of 127.0.0.1. Cost: Windows may
raise a firewall prompt on first run; allow it on private networks.
"""
import socket
import uuid

from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf

SERVICE_TYPE = "_yoink._tcp.local."


def lan_ip():
    """This machine's address on the LAN. The UDP 'connect' sends nothing — it
    just asks the routing table which local interface would reach the outside."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"        # no network: loopback is all we have
    finally:
        s.close()


class Discovery:
    def __init__(self, name, device_id, port, on_peer_up, on_peer_down):
        self.name = name
        self.device_id = device_id            # stable; what the mesh keys on
        self.port = port
        self.on_peer_up = on_peer_up          # (device_id, host, port)
        self.on_peer_down = on_peer_down      # (device_id)
        self.label = f"{name}-{uuid.uuid4().hex[:8]}"   # unique per launch
        self._seen = {}                       # service name -> device_id
        self._zc = Zeroconf()
        self._info = None

    def start(self):
        self._info = ServiceInfo(
            SERVICE_TYPE,
            f"{self.label}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(lan_ip())],
            port=self.port,
            properties={"name": self.name, "device": self.device_id},
            server=f"{self.label}.local.",
        )
        self._zc.register_service(self._info)
        ServiceBrowser(self._zc, SERVICE_TYPE, handlers=[self._on_change])

    def close(self):
        if self._info:
            self._zc.unregister_service(self._info)
        self._zc.close()

    def _on_change(self, zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Removed:
            # Removals carry no TXT record, so map the service name back to the
            # device_id we recorded when it appeared.
            peer_id = self._seen.pop(name, None)
            if peer_id:
                self.on_peer_down(peer_id)
            return

        info = zeroconf.get_service_info(service_type, name)
        if not info:
            return
        peer_id = _prop(info, "device")
        if not peer_id or peer_id == self.device_id:
            return                            # nameless, or our own service
        addrs = info.parsed_addresses()
        if addrs:
            self._seen[name] = peer_id
            self.on_peer_up(peer_id, addrs[0], info.port)


def _prop(info, key):
    val = info.properties.get(key.encode())
    return val.decode() if isinstance(val, bytes) else val
