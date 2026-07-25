/// mDNS peer discovery, browse-only.
///
/// The desktop advertises `_yoink._tcp` with a TXT record carrying its stable
/// `device` id and friendly `name` (see daemon/net/discovery.py). We only
/// browse: the phone is client-only, so it never advertises and the desktop
/// never dials it.
///
/// The service type string must match the Python side exactly. Python uses the
/// fully-qualified `_yoink._tcp.local.`; Bonsoir wants it without the domain.
library;

import 'dart:async';

import 'package:bonsoir/bonsoir.dart';

const String serviceType = '_yoink._tcp';

class Discovery {
  Discovery({required this.onPeerUp, required this.onPeerDown, required this.log});

  /// (deviceId, host, port) — the stable id from the TXT record, not the
  /// mDNS instance label, which changes on every daemon launch.
  final void Function(String deviceId, String host, int port) onPeerUp;
  final void Function(String deviceId) onPeerDown;
  final void Function(String msg) log;

  BonsoirDiscovery? _discovery;
  StreamSubscription<BonsoirDiscoveryEvent>? _sub;
  final _seen = <String, String>{}; // mDNS service name -> device id

  Future<void> start() async {
    final discovery = BonsoirDiscovery(type: serviceType);
    await discovery.initialize();
    _sub = discovery.eventStream?.listen(_onEvent);
    await discovery.start();
    _discovery = discovery;
    log('discovery on, browsing for $serviceType');
  }

  void _onEvent(BonsoirDiscoveryEvent event) {
    switch (event) {
      case BonsoirDiscoveryServiceFoundEvent():
        // Found only carries the name; ask for the address and TXT record.
        event.service.resolve(_discovery!.serviceResolver);
      case BonsoirDiscoveryServiceResolvedEvent():
        _resolved(event.service);
      case BonsoirDiscoveryServiceLostEvent():
        // Lost carries no TXT record, so map the service name back to the
        // device id we recorded when it appeared.
        final id = _seen.remove(event.service.name);
        if (id != null) onPeerDown(id);
      default:
        break;
    }
  }

  void _resolved(BonsoirService service) {
    final deviceId = service.attributes['device'];
    final host = service.hostAddress;
    if (deviceId == null || deviceId.isEmpty || host == null) {
      log('ignoring ${service.name}: no device id or address');
      return;
    }
    _seen[service.name] = deviceId;
    onPeerUp(deviceId, host, service.port);
  }

  Future<void> stop() async {
    await _sub?.cancel();
    await _discovery?.stop();
  }
}
