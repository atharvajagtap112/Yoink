/// One open WebSocket, with inbound envelopes buffered into a queue.
///
/// Python gets `await ws.recv()` for free from the websockets library. Dart
/// gives us a single-subscription Stream instead, so we attach exactly one
/// listener for the connection's whole life and park decoded envelopes here.
/// Buffering matters: during the pairing handshake a reply can land before the
/// next `next()` call, and a plain `stream.first` would drop it.
library;

import 'dart:async';

import 'package:web_socket_channel/web_socket_channel.dart';

import 'protocol.dart' as protocol;

class Link {
  Link(this.channel) {
    _sub = channel.stream.listen(
      (raw) {
        final env = protocol.decode(raw);
        if (env != null) _push(env);
      },
      onDone: _fail,
      onError: (_) => _fail(),
      cancelOnError: true,
    );
  }

  final WebSocketChannel channel;
  late final StreamSubscription<dynamic> _sub;
  final _ready = <Map<String, dynamic>>[];
  final _waiting = <Completer<Map<String, dynamic>>>[];
  bool _closed = false;

  /// Fires when the socket drops, for whatever reason.
  final Completer<void> done = Completer<void>();

  void _push(Map<String, dynamic> env) {
    if (_waiting.isNotEmpty) {
      _waiting.removeAt(0).complete(env);
    } else {
      _ready.add(env);
    }
  }

  void _fail() {
    if (_closed) return;
    _closed = true;
    for (final c in _waiting) {
      c.completeError(const SocketDropped());
    }
    _waiting.clear();
    if (!done.isCompleted) done.complete();
  }

  bool get isClosed => _closed;

  /// Next envelope off the wire. Throws [SocketDropped] if the socket closes
  /// first, so callers never hang on a dead link.
  Future<Map<String, dynamic>> next() {
    if (_ready.isNotEmpty) return Future.value(_ready.removeAt(0));
    if (_closed) return Future.error(const SocketDropped());
    final c = Completer<Map<String, dynamic>>();
    _waiting.add(c);
    return c.future;
  }

  void send(Map<String, dynamic> env) {
    if (_closed) return;
    channel.sink.add(protocol.encode(env));
  }

  Future<void> close() async {
    _fail();
    await _sub.cancel();
    await channel.sink.close();
  }
}

class SocketDropped implements Exception {
  const SocketDropped();
  @override
  String toString() => 'socket dropped';
}
