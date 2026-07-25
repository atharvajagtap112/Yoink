/// Screen capture over MediaProjection (DESIGN.md section 3, send path).
///
/// Android counterpart of daemon/grab/screenshot.py: the universal fallback for
/// "I couldn't work out what you're pointing at, so send the screen".
///
/// The native half lives in ScreenCaptureService.kt. Consent is a one-time
/// system dialog per app session; once granted the projection stays alive and
/// frames are cheap. If the user declines we remember that and stop asking, so
/// a denial costs one dialog, not one per gesture.
library;

import 'package:flutter/services.dart';

const MethodChannel _channel = MethodChannel('yoink/screencap');

/// Set once the user declines, so we never nag on later gestures.
bool _declined = false;

bool get declined => _declined;

Future<bool> isReady() async =>
    await _channel.invokeMethod<bool>('isReady') ?? false;

/// Ask for screen-capture consent if we don't already have it.
/// Returns false if the user declines or the platform refuses.
Future<bool> ensureConsent() async {
  if (_declined) return false;
  if (await isReady()) return true;
  final granted = await _channel.invokeMethod<bool>('requestConsent') ?? false;
  if (!granted) _declined = true;
  return granted;
}

/// One PNG frame of the current screen, or null if capture isn't available.
Future<Uint8List?> capture() async {
  if (!await ensureConsent()) return null;
  return _channel.invokeMethod<Uint8List>('capture');
}
