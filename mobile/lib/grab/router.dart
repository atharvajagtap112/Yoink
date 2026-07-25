/// Decide WHAT to grab on a SEND gesture (DESIGN.md section 3, send path).
///
/// The phone's counterpart to daemon/grab/router.py, and deliberately much
/// smaller. The desktop can inspect the foreground window, ask Explorer for a
/// real file path, or talk to a browser extension. Android gives an app none of
/// that: there is no cross-app foreground file access at all. So the ladder is
/// only two rungs:
///
///     clipboard has text -> http(s) url? send `url`, else send `text`
///     nothing usable     -> screenshot the screen -> send `image`
///
/// The screenshot plays exactly the role it does on the desktop: the universal
/// fallback that means a grab never comes up empty.
library;

import 'dart:convert';

import 'package:flutter/services.dart';

import '../config.dart';
import '../net/protocol.dart' as protocol;
import 'screenshot.dart' as screenshot;

/// Same shape as URL_RE in daemon/grab/router.py, so both ends agree on what
/// counts as a link rather than plain text.
final RegExp urlPattern = RegExp(r'^https?://\S+$', caseSensitive: false);

/// 'url' or 'text' for a non-empty clipboard string. Pure, so it's testable
/// without a platform channel.
String clipboardType(String text) =>
    urlPattern.hasMatch(text.trim()) ? 'url' : 'text';

/// Grab whatever the user is pointing at. Returns an envelope, or null if
/// there was nothing to send.
Future<Map<String, dynamic>?> grab({
  required String sender,
  required void Function(String) log,
}) async {
  final clip = await Clipboard.getData(Clipboard.kTextPlain);
  final text = clip?.text?.trim() ?? '';

  if (text.isNotEmpty) {
    final type = clipboardType(text);
    log('grab: strategy=clipboard $type (${text.length} chars)');
    return protocol.envelope('payload', type: type, data: text, sender: sender);
  }

  log('grab: clipboard empty -> screenshot (fallback)');
  final png = await screenshot.capture();
  if (png == null) {
    log(
      screenshot.declined
          ? 'grab: screenshot unavailable (consent declined) — clipboard only'
          : 'grab: screenshot failed — nothing to grab',
    );
    return null;
  }
  if (png.length > maxGrabBytes) {
    log(
      'grab: screenshot is ${(png.length / 1e6).toStringAsFixed(1)} MB, over '
      'the ${(maxGrabBytes / 1e6).toStringAsFixed(0)} MB limit -> not sending',
    );
    return null;
  }
  log('grab: strategy=screenshot (${png.length} bytes)');
  return protocol.envelope(
    'payload',
    type: 'image',
    filename: 'screen.png',
    mime: 'image/png',
    data: base64Encode(png),
    sender: sender,
  );
}
