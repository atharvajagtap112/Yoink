/// Yoink mobile — milestone 7d: the loop runs both ways.
///
/// Camera -> hand_landmarker -> classifier -> state machine turns your hand into
/// SEND / RECEIVE (7a). mDNS discovery finds the desktop daemon, we dial it and
/// pair once with a PIN (7b).
///
///   fist (SEND)       -> grab the clipboard, else screenshot the screen, and
///                        broadcast it to every paired peer (7d)
///   open hand (RECEIVE) -> open the last caught payload by type: clipboard,
///                        browser, or the OS file association (7c)
library;

import 'dart:async';
import 'dart:io';
import 'dart:math' as math;

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:hand_landmarker/hand_landmarker.dart';

import 'config.dart';
import 'gesture/classifier.dart';
import 'gesture/state_machine.dart';
import 'grab/router.dart' as grab;
import 'grab/screenshot.dart' as screenshot;
import 'net/discovery.dart';
import 'net/mesh.dart';
import 'net/pairing.dart';
import 'net/protocol.dart' as protocol;
import 'receive/dispatch.dart' as receive;
import 'receive/paths.dart';
import 'receive/pop.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp]);
  runApp(const MaterialApp(title: 'Yoink', home: GestureView()));
}

class GestureView extends StatefulWidget {
  const GestureView({super.key});

  @override
  State<GestureView> createState() => _GestureViewState();
}

class _GestureViewState extends State<GestureView> {
  CameraController? _controller;
  HandLandmarkerPlugin? _plugin;
  StreamSubscription<List<Hand>>? _sub;

  final _sm = GestureStateMachine();
  List<Landmark> _landmarks = const [];
  Pose _pose = Pose.noHand;
  GestureEvent? _lastEvent;
  DateTime _lastEventAt = DateTime.fromMillisecondsSinceEpoch(0);
  String? _error;

  Mesh? _mesh;
  Discovery? _discovery;
  String _netStatus = 'starting...';
  int _livePeers = 0;
  // Only the last received payload is kept (DESIGN.md section 3: each receiver
  // holds its own copy of the last thing received).
  receive.Caught? _lastCaught;
  Directory? _saveDir;
  String _myName = defaultDeviceName; // the `sender` we stamp on what we send

  @override
  void initState() {
    super.initState();
    _start();
    _startNetwork();
  }

  Future<void> _start() async {
    try {
      // initialize() triggers Android's runtime permission prompt and throws
      // CameraAccessDenied if the user says no.
      final cameras = await availableCameras();
      final cam = cameras.firstWhere(
        (c) => c.lensDirection == CameraLensDirection.front,
        orElse: () => cameras.first,
      );
      final controller = CameraController(
        cam,
        ResolutionPreset.medium,
        enableAudio: false,
      );
      await controller.initialize();

      final plugin = HandLandmarkerPlugin.create(
        numHands: 1,
        minHandDetectionConfidence: 0.6,
        delegate: HandLandmarkerDelegate.gpu,
      );

      _sub = plugin.landmarkStream.listen(_onHands);
      await controller.startImageStream(
        (image) => plugin.processFrame(image, cam.sensorOrientation),
      );

      if (!mounted) return;
      setState(() {
        _controller = controller;
        _plugin = plugin;
      });
    } on CameraException catch (e) {
      setState(() => _error = e.code == 'CameraAccessDenied'
          ? 'Camera permission denied. Enable it in app settings, then reopen.'
          : 'Camera error: ${e.code} ${e.description ?? ''}');
    } catch (e) {
      setState(() => _error = '$e');
    }
  }

  Future<void> _startNetwork() async {
    final me = await loadIdentity();
    _myName = me.name;
    final store = await PairedStore.load();
    _log('I am ${me.name} (id ${me.deviceId.substring(0, 8)})');
    if (store.names.isNotEmpty) {
      _log('already paired with: ${store.names.values.toList()}');
    }

    final mesh = Mesh(
      name: me.name,
      deviceId: me.deviceId,
      store: store,
      onReceive: _onPayload,
      onStatus: _log,
      prompt: _askForPin,
    );
    final discovery = Discovery(
      onPeerUp: mesh.peerUp,
      onPeerDown: mesh.peerDown,
      log: _log,
    );
    try {
      await discovery.start();
    } catch (e) {
      _log('discovery failed to start: $e');
    }
    if (!mounted) return;
    setState(() {
      _mesh = mesh;
      _discovery = discovery;
    });
  }

  void _log(String msg) {
    debugPrint('[net] $msg');
    if (!mounted) return;
    setState(() {
      _netStatus = msg;
      _livePeers = _mesh?.peers.length ?? 0;
    });
  }

  /// A payload landed over the network. Decode and save it now so the pop can
  /// show a real thumbnail, but don't open anything — that waits for the
  /// RECEIVE gesture or a tap, so nothing launches an app behind your back.
  Future<void> _onPayload(Map<String, dynamic> env) async {
    final saveDir = _saveDir ??= await receivedDir();
    final caught = await receive.prepare(env, saveDir: saveDir, log: _log);
    if (caught == null || !mounted) return;
    setState(() => _lastCaught = caught);
    showPop(context, caught, onOpen: _openLast);
  }

  /// Closed hand: grab something and throw it to every paired peer.
  Future<void> _doSend() async {
    final mesh = _mesh;
    if (mesh == null || mesh.peers.isEmpty) {
      _log('broadcast: no paired peers yet — nothing sent');
      _snack('no paired peers — nothing sent');
      return;
    }
    final env = await grab.grab(sender: _myName, log: _log);
    if (env == null) {
      _snack(
        screenshot.declined
            ? 'nothing to grab (screen capture is off)'
            : 'nothing to grab',
      );
      return;
    }
    mesh.broadcast(env);
    final what = protocol.describe(env);
    _log('SENT -> $what to ${mesh.peers.length} peer(s)');
    _snack('sent: $what');
  }

  void _snack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(
        SnackBar(content: Text(msg), duration: const Duration(seconds: 3)),
      );
  }

  /// Open the last caught payload by type. Fired by the RECEIVE gesture and by
  /// tapping the pop.
  Future<void> _openLast() async {
    final caught = _lastCaught;
    if (caught == null) {
      _log('RECEIVE: nothing received yet');
      return;
    }
    final what = await receive.open(caught, log: _log);
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(
        SnackBar(content: Text(what), duration: const Duration(seconds: 3)),
      );
  }

  /// The desktop prints the PIN on its console; the user types it here.
  Future<String?> _askForPin(String peerName) {
    final controller = TextEditingController();
    return showDialog<String>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: Text('Pair with $peerName?'),
        content: TextField(
          controller: controller,
          autofocus: true,
          keyboardType: TextInputType.number,
          decoration: InputDecoration(
            labelText: 'PIN shown on $peerName',
            hintText: '0000',
          ),
          onSubmitted: (v) => Navigator.pop(ctx, v),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, controller.text),
            child: const Text('Pair'),
          ),
        ],
      ),
    );
  }

  void _onHands(List<Hand> hands) {
    final lm = hands.isEmpty ? const <Landmark>[] : hands.first.landmarks;
    final pose = lm.isEmpty
        ? Pose.noHand
        : classify([for (final p in lm) Offset(p.x, p.y)]);
    final event = _sm.update(pose);
    if (event != null) debugPrint('gesture: ${event.name.toUpperCase()}');
    if (!mounted) return;
    setState(() {
      _landmarks = lm;
      _pose = pose;
      if (event != null) {
        _lastEvent = event;
        _lastEventAt = DateTime.now();
      }
    });
    // Fist = grab and throw, open hand = catch. The two halves of the loop.
    if (event == GestureEvent.send) unawaited(_doSend());
    if (event == GestureEvent.receive) unawaited(_openLast());
  }

  @override
  void dispose() {
    _sub?.cancel();
    _controller?.dispose();
    _plugin?.dispose();
    _discovery?.stop();
    _mesh?.stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return Scaffold(
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text(_error!, textAlign: TextAlign.center),
          ),
        ),
      );
    }
    final controller = _controller;
    if (controller == null) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }

    final preview = controller.value.previewSize!;
    // Linger the last event ~1s so it's readable, not a one-frame flash.
    final fresh = _lastEvent != null &&
        DateTime.now().difference(_lastEventAt) < const Duration(seconds: 1);

    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        fit: StackFit.expand,
        children: [
          Center(
            child: AspectRatio(
              aspectRatio: preview.height / preview.width,
              child: Stack(
                children: [
                  CameraPreview(controller),
                  CustomPaint(
                    size: Size.infinite,
                    painter: _LandmarkPainter(
                      landmarks: _landmarks,
                      previewSize: preview,
                      lensDirection: controller.description.lensDirection,
                      sensorOrientation:
                          controller.description.sensorOrientation,
                    ),
                  ),
                ],
              ),
            ),
          ),
          Positioned(
            top: 48,
            left: 20,
            child: Text(
              _pose.name.toUpperCase(),
              style: TextStyle(
                fontSize: 34,
                fontWeight: FontWeight.bold,
                color: switch (_pose) {
                  Pose.open => Colors.greenAccent,
                  Pose.closed => Colors.orangeAccent,
                  _ => Colors.white54,
                },
              ),
            ),
          ),
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: Container(
              color: Colors.black54,
              padding: const EdgeInsets.fromLTRB(16, 10, 16, 24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    _livePeers > 0
                        ? 'mesh: $_livePeers peer(s) connected'
                        : 'mesh: looking for peers...',
                    style: TextStyle(
                      color: _livePeers > 0
                          ? Colors.greenAccent
                          : Colors.orangeAccent,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  if (_lastCaught != null)
                    GestureDetector(
                      onTap: _openLast,
                      child: Text(
                        'caught: ${_lastCaught!.label}  (open hand to open)',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: Colors.white),
                      ),
                    ),
                  const SizedBox(height: 2),
                  Text(
                    _netStatus,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: Colors.white70, fontSize: 12),
                  ),
                ],
              ),
            ),
          ),
          if (fresh)
            Center(
              child: Text(
                _lastEvent!.name.toUpperCase(),
                style: const TextStyle(
                  fontSize: 64,
                  fontWeight: FontWeight.bold,
                  color: Colors.redAccent,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// Landmarks + skeleton over the preview. Transform lifted from the
/// hand_landmarker example: it maps normalized coords through the sensor
/// rotation and the front-camera mirror.
class _LandmarkPainter extends CustomPainter {
  _LandmarkPainter({
    required this.landmarks,
    required this.previewSize,
    required this.lensDirection,
    required this.sensorOrientation,
  });

  final List<Landmark> landmarks;
  final Size previewSize;
  final CameraLensDirection lensDirection;
  final int sensorOrientation;

  static const List<List<int>> _connections = [
    [0, 1], [1, 2], [2, 3], [3, 4], // thumb
    [0, 5], [5, 6], [6, 7], [7, 8], // index
    [5, 9], [9, 10], [10, 11], [11, 12], // middle
    [9, 13], [13, 14], [14, 15], [15, 16], // ring
    [13, 17], [0, 17], [17, 18], [18, 19], [19, 20], // pinky
  ];

  @override
  void paint(Canvas canvas, Size size) {
    if (landmarks.length < 21) return;
    final scale = size.width / previewSize.height;

    final dot = Paint()..color = Colors.red;
    final line = Paint()
      ..color = Colors.lightBlueAccent
      ..strokeWidth = 4 / scale;

    canvas.save();
    canvas.translate(size.width / 2, size.height / 2);
    canvas.rotate(sensorOrientation * math.pi / 180);
    if (lensDirection == CameraLensDirection.front) {
      canvas.scale(-1, 1);
      canvas.rotate(math.pi);
    }
    canvas.scale(scale);

    Offset at(Landmark l) => Offset(
          (l.x - 0.5) * previewSize.width,
          (l.y - 0.5) * previewSize.height,
        );

    for (final c in _connections) {
      canvas.drawLine(at(landmarks[c[0]]), at(landmarks[c[1]]), line);
    }
    for (final l in landmarks) {
      canvas.drawCircle(at(l), 8 / scale, dot);
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant _LandmarkPainter old) => true;
}
