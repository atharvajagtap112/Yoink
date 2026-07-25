/// Where caught files land, and how their names are made safe.
///
/// Port of safe_filename()/unique_path()/save_dir() from daemon/config.py.
///
/// The filename arrives from another machine, so it is untrusted: '../../evil'
/// or '/data/data/other.app/x' must not escape the save directory. We keep only
/// the last path component and scrub what a filesystem won't accept.
library;

import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// Control chars plus the characters Windows rejects — kept identical to the
/// Python side so a name that survives one survives the other. Separators are
/// included as a belt-and-braces second line of defence; the split below has
/// already removed them.
final _badChars = RegExp(r'[<>:"|?*/\\\x00-\x1f]');

String safeFilename(String? name, {String fallback = 'yoink-payload'}) {
  if (name == null || name.isEmpty) return fallback;
  var n = name.replaceAll('\\', '/').split('/').last; // drop any path part
  n = n.replaceAll(_badChars, '_').trim();
  while (n.isNotEmpty && (n.endsWith('.') || n.endsWith(' '))) {
    n = n.substring(0, n.length - 1);
  }
  if (n.isEmpty) return fallback; // was '', '.', '..' or all dots
  return n.length > 120 ? n.substring(0, 120) : n;
}

/// A path that doesn't clobber an existing file: photo.png -> photo (1).png.
File uniquePath(Directory dir, String filename) {
  final first = File('${dir.path}/$filename');
  if (!first.existsSync()) return first;

  final dot = filename.lastIndexOf('.');
  final stem = dot > 0 ? filename.substring(0, dot) : filename;
  final ext = dot > 0 ? filename.substring(dot) : '';
  for (var i = 1; i < 1000; i++) {
    final p = File('${dir.path}/$stem ($i)$ext');
    if (!p.existsSync()) return p;
  }
  throw FileSystemException('too many files named like', filename);
}

/// The app-specific external directory, so no storage permission is needed and
/// open_filex's FileProvider (external-files-path) can hand the file to other
/// apps. Falls back to internal storage if there's no external volume.
Future<Directory> receivedDir() async {
  final base =
      await getExternalStorageDirectory() ??
      await getApplicationDocumentsDirectory();
  final d = Directory('${base.path}/received');
  await d.create(recursive: true);
  return d;
}
