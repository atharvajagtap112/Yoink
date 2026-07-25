/// PIN pairing: the trust gate that must pass before any payload is accepted.
///
/// Port of the *dialer* half of daemon/net/pairing.py. The phone is client-only
/// (it never listens), so it is always the dialing side and never the one that
/// shows a PIN — the desktop prints the PIN on its console and you type it here.
/// Only the guess crosses the wire, so sniffing the traffic doesn't reveal it.
///
/// Handshake, dialer's view:
///     me      -> hello   (data = my device_id, sender = my name)
///     peer    -> hello
///     me      -> pair    type=known|new   ("do I already have you stored?")
///     peer    -> pair    type=ok          -> already trusted, done
///                   or   type=request     -> peer is showing a PIN
///     me      -> pair    type=confirm, data=`<pin the user typed>`
///     peer    -> pair    type=ok | deny
library;

import 'dart:async';

import 'package:shared_preferences/shared_preferences.dart';

import 'link.dart';
import 'protocol.dart' as protocol;

/// Generous: a human is reading a PIN off a laptop screen and typing it.
const Duration pairTimeout = Duration(seconds: 120);

/// The peers we've already trusted, persisted so pairing is one-time.
/// Mirrors PairedStore in daemon/net/pairing.py, backed by shared_preferences
/// instead of a JSON file.
class PairedStore {
  PairedStore._(this._prefs, this._peers);

  static const _key = 'yoink.paired';

  final SharedPreferences _prefs;
  final Map<String, String> _peers; // device_id -> name

  static Future<PairedStore> load() async {
    final prefs = await SharedPreferences.getInstance();
    final peers = <String, String>{};
    for (final entry in prefs.getStringList(_key) ?? const <String>[]) {
      final i = entry.indexOf('=');
      if (i > 0) peers[entry.substring(0, i)] = entry.substring(i + 1);
    }
    return PairedStore._(prefs, peers);
  }

  bool isPaired(String deviceId) => _peers.containsKey(deviceId);

  Map<String, String> get names => Map.unmodifiable(_peers);

  Future<void> add(String deviceId, String name) async {
    _peers[deviceId] = name;
    await _prefs.setStringList(
      _key,
      [for (final e in _peers.entries) '${e.key}=${e.value}'],
    );
  }

  Future<void> forgetAll() async {
    _peers.clear();
    await _prefs.remove(_key);
  }
}

/// A trusted peer's identity, returned once the handshake passes.
class PairResult {
  const PairResult(this.deviceId, this.name);
  final String deviceId;
  final String name;
}

void _sendPair(Link link, String step, String meName, [String? pin]) =>
    link.send(protocol.envelope('pair', type: step, data: pin, sender: meName));

/// Read until a message of [kind] arrives.
///
/// Payloads that show up before pairing is done are refused here — this
/// rejection is the whole reason the feature exists.
Future<Map<String, dynamic>> _recvKind(
  Link link,
  String kind,
  void Function(String) log,
) async {
  while (true) {
    final env = await link.next().timeout(pairTimeout);
    if (env['kind'] == 'payload') {
      log('REJECTED payload from unpaired peer ${env['sender']}');
      continue;
    }
    if (env['kind'] == kind) return env;
    // anything else (e.g. a stray heartbeat) is ignored during the handshake
  }
}

/// Run the handshake on a fresh connection.
///
/// Returns the peer if it is trusted and may join the mesh, or null if the
/// connection must be dropped.
Future<PairResult?> handshake(
  Link link, {
  required String meName,
  required String meId,
  required PairedStore store,
  required void Function(String) log,
  required Future<String?> Function(String peerName) prompt,
}) async {
  link.send(protocol.envelope('hello', data: meId, sender: meName));
  final hello = await _recvKind(link, 'hello', log);

  final peerId = hello['data'] as String?;
  final peerName = (hello['sender'] as String?) ?? 'unknown';
  if (peerId == null || peerId.isEmpty || peerId == meId) {
    return null; // nameless, or ourselves
  }

  _sendPair(link, store.isPaired(peerId) ? 'known' : 'new', meName);
  final resp = await _recvKind(link, 'pair', log);
  final step = resp['type'];

  if (step == 'ok') {
    return PairResult(peerId, peerName); // both sides already trusted
  }
  if (step != 'request') return null;

  log('PAIR REQUEST from $peerName — enter the PIN shown on its console');
  final pin = await prompt(peerName);
  if (pin == null) {
    log('pairing cancelled');
    return null;
  }
  _sendPair(link, 'confirm', meName, pin.trim());

  if ((await _recvKind(link, 'pair', log))['type'] != 'ok') {
    log('PAIR DENIED by $peerName (wrong PIN)');
    return null;
  }
  await store.add(peerId, peerName);
  log('PAIRED with $peerName (saved; won\'t ask again)');
  return PairResult(peerId, peerName);
}
