/// The phone's half of the mesh: dial discovered desktops, pair, receive.
///
/// Port of daemon/net/mesh.py with one deliberate difference: **the phone is
/// client-only**. It never listens and never advertises, so:
///   * the "lower device_id dials" dedupe rule from the Python side doesn't
///     apply — the desktop can't discover us, so it can never dial us, so there
///     is no second socket to dedupe. We always dial.
///   * one socket carries both directions. The desktop mesh broadcasts to every
///     live connection, so our inbound dial receives payloads with no changes
///     on the desktop side.
///
/// Reconnect: unlike the Python version, a dial loop is *not* torn down when
/// mDNS says the service went away. On a phone, "service lost" usually means
/// WiFi blipped, not that the desktop is gone, and NsdManager can be slow to
/// re-announce. Keeping the loop retrying against the last known address is
/// what makes "drop WiFi, bring it back" rejoin on its own.
/// ponytail: retry-forever against the last address; fine for a handful of
/// desktops. If a desktop's LAN IP changes while it's down, discovery updates
/// the target on its next announcement.
library;

import 'dart:async';

import 'package:web_socket_channel/web_socket_channel.dart';

import 'link.dart';
import 'pairing.dart';
import 'peer.dart';
import 'protocol.dart' as protocol;

const Duration retryDelay = Duration(seconds: 1);

/// After a failed pairing, so a denial can't spam the PIN prompt.
const Duration pairRetryDelay = Duration(seconds: 30);

const Duration connectTimeout = Duration(seconds: 5);

class Mesh {
  Mesh({
    required this.name,
    required this.deviceId,
    required this.store,
    required this.onReceive,
    required this.onStatus,
    required this.prompt,
  });

  final String name;
  final String deviceId;
  final PairedStore store;
  final void Function(Map<String, dynamic> env) onReceive;
  final void Function(String msg) onStatus;
  final Future<String?> Function(String peerName) prompt;

  final peers = <String, Peer>{}; // device_id -> Peer (paired + live only)
  final _targets = <String, ({String host, int port})>{};
  final _dialers = <String>{};
  bool _stopped = false;

  /// One PIN at a time: the dialog is a single shared resource, and pairing
  /// with two desktops at once would land your answer in the wrong handshake.
  Future<void> _promptGate = Future.value();

  // --- discovery callbacks ---

  void peerUp(String peerId, String host, int port) {
    final target = (host: host, port: port);
    final known = _targets[peerId];
    _targets[peerId] = target;
    if (_dialers.contains(peerId)) {
      if (known != target) onStatus('$peerId moved to $host:$port');
      return; // already dialing; the loop picks up the new address
    }
    onStatus('discovered ${peerId.substring(0, 8)} at $host:$port — dialing');
    _dialers.add(peerId);
    unawaited(_dialLoop(peerId));
  }

  void peerDown(String peerId) {
    // Keep the target and the dial loop (see the reconnect note above).
    onStatus('peer gone from discovery: ${peerId.substring(0, 8)} — still retrying');
  }

  // --- connection lifecycle ---

  Future<void> _dialLoop(String peerId) async {
    while (!_stopped) {
      final target = _targets[peerId];
      if (target == null || peers.containsKey(peerId)) {
        await Future<void>.delayed(retryDelay);
        continue;
      }
      var paired = true;
      try {
        final channel = WebSocketChannel.connect(
          Uri.parse('ws://${target.host}:${target.port}'),
        );
        await channel.ready.timeout(connectTimeout);
        paired = await _session(Link(channel));
      } catch (e) {
        // connect refused / timed out / WiFi gone: just retry
      }
      await Future<void>.delayed(paired ? retryDelay : pairRetryDelay);
    }
    _dialers.remove(peerId);
  }

  /// Pair, then serve the connection until it drops. Returns false only when
  /// pairing itself failed, so the dial loop can back off instead of
  /// re-prompting for a PIN every second.
  Future<bool> _session(Link link) async {
    PairResult? res;
    try {
      res = await _pairOneAtATime(link);
    } on SocketDropped {
      return true;
    } on TimeoutException {
      return true;
    }
    if (res == null) {
      await link.close();
      return false;
    }

    if (peers.containsKey(res.deviceId)) {
      onStatus('duplicate link to ${res.name} — dropping the extra');
      await link.close();
      return true;
    }

    final peer = Peer(
      link,
      deviceId: res.deviceId,
      name: res.name,
      meName: name,
      onReceive: onReceive,
      onStatus: onStatus,
    );
    peers[res.deviceId] = peer;
    onStatus('peer UP: ${res.name} (${peers.length} live)');
    try {
      await peer.run();
    } finally {
      peers.remove(res.deviceId);
      onStatus('peer DOWN: ${res.name} (${peers.length} live)');
    }
    return true;
  }

  Future<PairResult?> _pairOneAtATime(Link link) {
    final gate = _promptGate;
    final mine = Completer<void>();
    _promptGate = mine.future;
    return gate.then((_) async {
      try {
        return await handshake(
          link,
          meName: name,
          meId: deviceId,
          store: store,
          log: onStatus,
          prompt: prompt,
        );
      } finally {
        mine.complete();
      }
    });
  }

  /// Fan a payload out to every paired peer. Unused in 7b (the phone doesn't
  /// grab yet) but the socket is full-duplex and ready for 7d.
  void broadcast(Map<String, dynamic> env) {
    for (final p in peers.values) {
      p.send(env);
    }
  }

  Future<void> stop() async {
    _stopped = true;
    for (final p in peers.values) {
      await p.link.close();
    }
  }
}

/// Re-exported so main.dart doesn't need to import protocol just for this.
String describe(Map<String, dynamic> env) => protocol.describe(env);
