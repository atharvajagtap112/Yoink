/// Guards wire compatibility with daemon/net/protocol.py.
///
/// The interop cases below are literal strings produced by the Python side —
/// if the desktop's envelope shape ever drifts, these fail here rather than
/// silently on the phone.
/// Run: flutter test
library;

import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:yoink_mobile/net/protocol.dart';

void main() {
  test('round trip', () {
    final e = envelope('payload', type: 'text', data: 'hello world', sender: 'laptop-a');
    expect(decode(encode(e)), e);
    expect(e['kind'], 'payload');
    expect(e['type'], 'text');
  });

  test('rejects non-envelopes', () {
    expect(decode('not json'), isNull);
    expect(decode('[1,2,3]'), isNull); // valid JSON, not an envelope
    expect(decode('{"foo": 1}'), isNull); // object but no kind
    expect(decode(<int>[1, 2, 3]), isNull); // a binary frame
  });

  test('every spec field is present and null when unset', () {
    final e = envelope('heartbeat', sender: 'phone');
    expect(e.keys.toSet(), {
      'v',
      'kind',
      'type',
      'filename',
      'mime',
      'data',
      'sender',
      'ts',
    });
    expect(e['v'], 1);
    expect(e['type'], isNull);
    expect(e['filename'], isNull);
    expect(e['mime'], isNull);
  });

  test('decodes what the Python daemon actually sends', () {
    // Exactly the shape of protocol.envelope() in daemon/net/protocol.py,
    // taken from CLAUDE.md section 6.
    const fromPython =
        '{"v": 1, "kind": "payload", "type": "url", "filename": null, '
        '"mime": null, "data": "https://youtube.com/watch?v=abc&t=142s", '
        '"sender": "atharva-laptop", "ts": 1720900000}';
    final env = decode(fromPython)!;
    expect(env['kind'], 'payload');
    expect(env['type'], 'url');
    expect(env['data'], 'https://youtube.com/watch?v=abc&t=142s');
    expect(env['sender'], 'atharva-laptop');
    expect(describe(env), startsWith('url https://youtube.com'));
  });

  test('image payload keeps base64 intact and describes by filename', () {
    final b64 = base64Encode(List<int>.generate(300, (i) => i % 256));
    const fromPython = 'pair'; // sanity: unrelated kinds still decode
    expect(decode('{"kind": "$fromPython"}')?['kind'], 'pair');

    final env = decode(
      encode(
        envelope(
          'payload',
          type: 'image',
          data: b64,
          filename: 'shot.png',
          mime: 'image/png',
          sender: 'laptop',
        ),
      ),
    )!;
    expect(env['data'], b64); // untouched, ready for 7c to decode
    expect(describe(env), 'image shot.png');
  });
}
