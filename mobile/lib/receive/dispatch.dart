/// Type -> save + open by association (DESIGN.md section 3, receive path).
///
/// Port of daemon/receive/dispatch.py:
///     url   -> open in the default browser
///     image -> save to disk -> preview pop (tap to open full size)
///     file  -> save to disk -> open with the OS default app
///     text  -> write to the clipboard -> pop
///
/// The whole point: the sender already decided the type, so this side never
/// asks "what app opens a .docx". It hands the file to Android and lets the
/// MIME association answer. One dispatcher covers a PDF, a spreadsheet and a
/// photo without knowing anything about any of them.
///
/// Split into two phases, unlike the desktop's single dispatch():
///   [prepare] runs on arrival — decode and save, so the pop can show a real
///             thumbnail — but opens nothing.
///   [open]    runs on the RECEIVE gesture, or when you tap the pop.
/// The desktop can fuse these because a Tk pop is cheap to build from a path;
/// here the pop is a widget that needs the saved file up front. Keeping the
/// open separate also means an arriving payload never launches an app behind
/// your back — it waits for the gesture, exactly like the desktop's camera path.
library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:open_filex/open_filex.dart';
import 'package:url_launcher/url_launcher.dart';

import 'paths.dart';

/// One received payload, decoded and saved, ready to be opened.
class Caught {
  const Caught({
    required this.type,
    required this.label,
    required this.sender,
    this.file,
    this.text,
    this.url,
    this.mime,
  });

  final String type; // text | url | image | file
  final String label; // what the pop shows
  final String sender;
  final File? file; // set for image/file
  final String? text; // set for text (and for a url we refused to launch)
  final String? url; // set for a launchable http(s) url
  final String? mime; // from the envelope; null -> guess from the extension

  bool get isImage => type == 'image' && file != null;
}

/// Decode and save one arriving payload. Returns null if it isn't usable.
Future<Caught?> prepare(
  Map<String, dynamic> env, {
  required Directory saveDir,
  required void Function(String) log,
}) async {
  final kind = env['type'] as String?;
  final sender = (env['sender'] as String?) ?? '?';
  final data = env['data'] as String?;
  if (data == null) {
    log('dispatch: payload from $sender has no data — ignored');
    return null;
  }

  switch (kind) {
    case 'text':
      return Caught(
        type: 'text',
        label: _short(data),
        sender: sender,
        text: data,
      );

    case 'url':
      // Only ever hand http(s) to the OS. An exotic scheme would launch
      // whatever handler happens to be registered for it — not something to do
      // with a string that arrived over the network. Keep it as text.
      final lower = data.toLowerCase();
      if (!lower.startsWith('http://') && !lower.startsWith('https://')) {
        log('CAUGHT url <- $sender: ${_short(data)} isn\'t openable, copied instead');
        return Caught(
          type: 'text',
          label: _short(data),
          sender: sender,
          text: data,
        );
      }
      return Caught(
        type: 'url',
        label: _short(data),
        sender: sender,
        url: data,
      );

    case 'image':
    case 'file':
      final Uint8List raw;
      try {
        raw = base64Decode(data);
      } on FormatException catch (e) {
        log('dispatch: bad base64 in $kind from $sender: $e');
        return null;
      }
      final name = safeFilename(
        env['filename'] as String?,
        fallback: 'yoink-$kind',
      );
      final file = uniquePath(saveDir, name);
      await file.writeAsBytes(raw);
      final shortName = file.path.split('/').last;
      log('CAUGHT $kind <- $sender: saved $shortName (${raw.length} bytes)');
      return Caught(
        type: kind!,
        label: shortName,
        sender: sender,
        file: file,
        mime: env['mime'] as String?,
      );

    default:
      log('dispatch: unknown payload type $kind from $sender — ignored');
      return null;
  }
}

/// Act on a prepared payload: clipboard, browser, or the OS file association.
/// Returns a short line describing what happened, for the log and the pop.
Future<String> open(Caught caught, {required void Function(String) log}) async {
  switch (caught.type) {
    case 'text':
      await Clipboard.setData(ClipboardData(text: caught.text!));
      log('CAUGHT text <- ${caught.sender}: copied to clipboard');
      return 'copied to clipboard';

    case 'url':
      final uri = Uri.parse(caught.url!);
      final ok = await launchUrl(uri, mode: LaunchMode.externalApplication);
      log('CAUGHT url <- ${caught.sender}: ${ok ? 'opened' : 'no browser found'}');
      return ok ? 'opened in browser' : 'no app could open that link';

    case 'image':
    case 'file':
      // The envelope's mime wins; when it's absent, passing null lets
      // open_filex map the extension to a MIME type itself, so a payload that
      // arrived without one still opens.
      final res = await OpenFilex.open(caught.file!.path, type: caught.mime);
      log('CAUGHT ${caught.type} <- ${caught.sender}: open -> ${res.message}');
      return switch (res.type) {
        ResultType.done => 'opened ${caught.label}',
        ResultType.noAppToOpen => 'saved — no app installed for this type',
        ResultType.fileNotFound => 'saved file went missing',
        ResultType.permissionDenied => 'Android refused to open it',
        ResultType.error => 'could not open: ${res.message}',
      };

    default:
      return 'nothing to open';
  }
}

String _short(String s) => s.length > 40 ? '${s.substring(0, 40)}...' : s;
