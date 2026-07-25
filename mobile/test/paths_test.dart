/// Guards the untrusted-filename handling in lib/receive/paths.dart.
///
/// The filename arrives from another machine, so traversal and collision are
/// the two ways this hurts: escaping the save directory, or silently eating a
/// file you already caught. Mirrors the intent of config.safe_filename /
/// config.unique_path on the desktop.
/// Run: flutter test
library;

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:yoink_mobile/receive/paths.dart';

void main() {
  group('safeFilename', () {
    test('keeps ordinary names', () {
      expect(safeFilename('report.pdf'), 'report.pdf');
      expect(safeFilename('holiday photo.png'), 'holiday photo.png');
    });

    test('strips any path component', () {
      expect(safeFilename('../../etc/passwd'), 'passwd');
      expect(safeFilename(r'C:\windows\system32\evil.dll'), 'evil.dll');
      expect(safeFilename('/data/data/other.app/db'), 'db');
      expect(safeFilename(r'a/b\c/d.txt'), 'd.txt');
    });

    test('falls back when nothing usable survives', () {
      expect(safeFilename(null), 'yoink-payload');
      expect(safeFilename(''), 'yoink-payload');
      expect(safeFilename('..'), 'yoink-payload');
      expect(safeFilename('.'), 'yoink-payload');
      expect(safeFilename('...'), 'yoink-payload');
      expect(safeFilename('/'), 'yoink-payload');
      expect(safeFilename('x', fallback: 'yoink-image'), 'x');
      expect(safeFilename('..', fallback: 'yoink-image'), 'yoink-image');
    });

    test('scrubs characters a filesystem rejects', () {
      expect(safeFilename('a<b>c:d"e|f?g*h.txt'), 'a_b_c_d_e_f_g_h.txt');
      expect(safeFilename('bell\x07.txt'), 'bell_.txt'); // control chars too
    });

    test('trims trailing dots and spaces', () {
      expect(safeFilename('name.   '), 'name');
      expect(safeFilename('name...'), 'name');
    });

    test('caps the length', () {
      expect(safeFilename('${'a' * 500}.png').length, 120);
    });

    test('the result can never escape its directory', () {
      const nasty = [
        '../../../etc/passwd',
        r'..\..\windows\evil.exe',
        '....//....//x',
        '/absolute/path',
        '..',
      ];
      for (final n in nasty) {
        final safe = safeFilename(n);
        expect(safe, isNot(contains('/')), reason: n);
        expect(safe, isNot(contains(r'\')), reason: n);
        expect(safe, isNot('..'), reason: n);
      }
    });
  });

  group('uniquePath', () {
    late Directory dir;

    setUp(() => dir = Directory.systemTemp.createTempSync('yoink_test'));
    tearDown(() => dir.deleteSync(recursive: true));

    test('uses the plain name when it is free', () {
      expect(uniquePath(dir, 'a.png').path, '${dir.path}/a.png');
    });

    test('never overwrites an existing catch', () {
      File('${dir.path}/photo.png').writeAsStringSync('first');
      final second = uniquePath(dir, 'photo.png');
      expect(second.path, '${dir.path}/photo (1).png');

      second.writeAsStringSync('second');
      expect(uniquePath(dir, 'photo.png').path, '${dir.path}/photo (2).png');
      // The original survived, which is the whole point.
      expect(File('${dir.path}/photo.png').readAsStringSync(), 'first');
    });

    test('handles names with no extension', () {
      File('${dir.path}/README').writeAsStringSync('x');
      expect(uniquePath(dir, 'README').path, '${dir.path}/README (1)');
    });

    test('treats a leading dot as part of the name, not an extension', () {
      File('${dir.path}/.gitignore').writeAsStringSync('x');
      expect(uniquePath(dir, '.gitignore').path, '${dir.path}/.gitignore (1)');
    });
  });
}
