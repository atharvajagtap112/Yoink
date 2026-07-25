/// Device identity, persisted so pairing survives a restart.
///
/// Mirrors device_id()/--name in daemon/config.py. The id is what pairing keys
/// on, so it must be stable across launches; the name is only cosmetic (it's
/// the `sender` field other devices display).
library;

import 'dart:math';

import 'package:shared_preferences/shared_preferences.dart';

const String defaultDeviceName = 'yoink-phone';

/// Biggest thing a grab will send, mirroring MAX_GRAB_BYTES in daemon/config.py.
/// Base64 over a WebSocket costs ~4/3 the bytes and the whole envelope is
/// buffered in memory on both ends, so this stays modest — it's a clipboard,
/// not a file-sync tool. Must stay under the desktop's MAX_WS_MESSAGE (48 MiB).
const int maxGrabBytes = 25 * 1000 * 1000;

class Identity {
  const Identity(this.name, this.deviceId);
  final String name;
  final String deviceId;
}

Future<Identity> loadIdentity() async {
  final prefs = await SharedPreferences.getInstance();
  var id = prefs.getString('yoink.device_id');
  if (id == null || id.isEmpty) {
    // 12 hex chars, same shape as the Python side's uuid4().hex[:12].
    final rng = Random.secure();
    id = List.generate(12, (_) => '0123456789abcdef'[rng.nextInt(16)]).join();
    await prefs.setString('yoink.device_id', id);
  }
  return Identity(prefs.getString('yoink.name') ?? defaultDeviceName, id);
}
