/// Yoink mobile — milestone 7a: gesture detection standalone.
///
/// Camera -> hand_landmarker -> classifier -> state machine. Draws the landmarks
/// and current pose over the preview, and flashes SEND / RECEIVE when an edge
/// fires. Nothing is sent anywhere yet (that's 7b).
library;

import 'dart:async';
import 'dart:math' as math;

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:hand_landmarker/hand_landmarker.dart';

import 'gesture/classifier.dart';
import 'gesture/state_machine.dart';

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

  @override
  void initState() {
    super.initState();
    _start();
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
  }

  @override
  void dispose() {
    _sub?.cancel();
    _controller?.dispose();
    _plugin?.dispose();
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
