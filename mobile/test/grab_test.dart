/// Guards the clipboard url-vs-text decision in lib/grab/router.dart.
///
/// The desktop decides the same thing with URL_RE in daemon/grab/router.py.
/// If the two disagree, a link sent from the phone arrives as plain text and
/// the laptop copies it instead of opening it — a silent, annoying failure.
/// Run: flutter test
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:yoink_mobile/grab/router.dart';

void main() {
  test('http(s) links are sent as url', () {
    for (final s in [
      'https://example.com',
      'http://example.com',
      'https://youtube.com/watch?v=abc&t=142s',
      'HTTPS://EXAMPLE.COM', // the desktop regex is case-insensitive too
      'https://example.com/a/b?c=d#e',
    ]) {
      expect(clipboardType(s), 'url', reason: s);
    }
  });

  test('surrounding whitespace does not change the verdict', () {
    expect(clipboardType('  https://example.com \n'), 'url');
  });

  test('anything else is sent as text', () {
    for (final s in [
      'hello world',
      'ftp://example.com', // not http(s): the desktop would not open it either
      'file:///C:/x.pdf',
      'example.com', // no scheme
      'see https://example.com for details', // a sentence, not a bare link
      'https://example.com and more', // trailing content -> \S+ fails
      'chrome://settings',
    ]) {
      expect(clipboardType(s), 'text', reason: s);
    }
  });
}
