/// JSON wire envelope (CLAUDE.md section 6). Encode/decode only — no I/O.
///
/// Direct port of daemon/net/protocol.py. The desktop daemon is the reference
/// implementation; these field names and types must match it exactly or the
/// two sides stop understanding each other.
library;

import 'dart:convert';

const int protocolVersion = 1;

/// Build a full envelope. Unused fields stay null, per the spec.
Map<String, dynamic> envelope(
  String kind, {
  String? type, // url | text | image | file  (payload only)
  String? data, // raw string for url/text; base64 for image/file
  String sender = '',
  String? filename,
  String? mime,
  int? ts,
}) => {
  'v': protocolVersion,
  'kind': kind, // hello | pair | heartbeat | payload
  'type': type,
  'filename': filename,
  'mime': mime,
  'data': data,
  'sender': sender,
  'ts': ts ?? DateTime.now().millisecondsSinceEpoch ~/ 1000,
};

String encode(Map<String, dynamic> env) => jsonEncode(env);

/// Parse a wire message. Returns the map, or null if it isn't a valid envelope
/// (bad JSON / not an object / missing kind).
Map<String, dynamic>? decode(Object? raw) {
  if (raw is! String) return null; // binary frames aren't part of the protocol
  Object? parsed;
  try {
    parsed = jsonDecode(raw);
  } on FormatException {
    return null;
  }
  if (parsed is! Map<String, dynamic> || !parsed.containsKey('kind')) {
    return null;
  }
  return parsed;
}

/// Short human-readable description of a payload, for logs and the toast.
/// image/file carry base64 in `data`, so show the filename instead of 40
/// characters of base64.
String describe(Map<String, dynamic> env) {
  final type = env['type'] ?? '?';
  final filename = env['filename'] as String?;
  if (filename != null && filename.isNotEmpty) return '$type $filename';
  final data = (env['data'] as String?) ?? '';
  final head = data.length > 40 ? '${data.substring(0, 40)}...' : data;
  return '$type $head';
}
