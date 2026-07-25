/// One paired WebSocket connection, with its own heartbeat lifecycle.
///
/// Port of daemon/net/peer.py — same intervals, so both ends agree on when a
/// link is dead. A Peer only exists once pairing has passed, so anything holding
/// a Peer is already trusted.
///
/// Heartbeats: send a `heartbeat` envelope every [heartbeatInterval]; any
/// inbound message refreshes "last heard". Silent for [deadAfter] (~3 missed
/// beats) and the link is declared dead and closed. A peer whose process is
/// killed closes the socket cleanly and is noticed immediately — the heartbeat
/// is for the ungraceful case (frozen peer, dropped WiFi) where the socket is
/// left half-open. On a phone that is the common case, not the rare one.
library;

import 'dart:async';

import 'link.dart';
import 'protocol.dart' as protocol;

const Duration heartbeatInterval = Duration(seconds: 3);
const Duration deadAfter = Duration(seconds: 10);

class Peer {
  Peer(
    this.link, {
    required this.deviceId,
    required this.name,
    required this.meName,
    required this.onReceive,
    required this.onStatus,
  });

  final Link link;
  final String deviceId;
  final String name;
  final String meName;
  final void Function(Map<String, dynamic> env) onReceive;
  final void Function(String msg) onStatus;

  final _clock = Stopwatch()..start();
  Duration _lastHeard = Duration.zero;

  void send(Map<String, dynamic> env) => link.send(env);

  /// Serve this connection until it closes. Returns when the peer is gone.
  Future<void> run() async {
    final hb = Timer.periodic(heartbeatInterval, (t) {
      if (_clock.elapsed - _lastHeard > deadAfter) {
        onStatus('heartbeat lost — dropping $name');
        t.cancel();
        link.close();
        return;
      }
      link.send(protocol.envelope('heartbeat', sender: meName));
    });

    try {
      while (true) {
        final env = await link.next();
        _lastHeard = _clock.elapsed;
        if (env['kind'] == 'payload') onReceive(env);
        // heartbeats need no handling; refreshing _lastHeard above is the point
      }
    } on SocketDropped {
      // normal end of life for this link
    } finally {
      hb.cancel();
      await link.close();
    }
  }
}
