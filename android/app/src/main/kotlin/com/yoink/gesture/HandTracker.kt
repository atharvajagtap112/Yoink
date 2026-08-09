package com.yoink.gesture

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.core.Delegate
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarker
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarkerResult
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Camera frames -> MediaPipe hand landmarks -> classifier -> state machine.
 *
 * The Kotlin equivalent of daemon/gesture/camera.py, and the reason this client
 * exists in Kotlin at all: CameraX is bound to a LifecycleOwner we choose, so a
 * service can own the camera and keep it streaming while the app is in the
 * background. The Flutter camera plugin binds to the Activity and cannot.
 *
 * Landmarks arrive on a MediaPipe worker thread; [onFrame] is invoked there, so
 * callers must hop to the main thread before touching UI.
 */
class HandTracker(
    private val context: Context,
    private val onFrame: (landmarks: List<Point>, pose: Pose, event: GestureEvent?) -> Unit,
) {
    private val stateMachine = GestureStateMachine()
    private var landmarker: HandLandmarker? = null
    private var analysisExecutor: ExecutorService? = null
    private var provider: ProcessCameraProvider? = null
    private var preview: Preview? = null

    /** Mirrors the desktop's cv2.flip: the preview should read like a mirror. */
    private var isFront = true

    /**
     * Bind the camera to [owner]'s lifecycle. Pass the *service* as the owner
     * and the camera keeps streaming while the Activity is gone.
     *
     * The Preview use case is bound here too, with no surface attached. A
     * Preview without a surface provider simply doesn't render, so the UI can
     * come and go via [setSurfaceProvider] without rebinding the camera —
     * rebinding would stall the analysis stream and drop gesture frames.
     */
    fun start(owner: LifecycleOwner, front: Boolean = true) {
        isFront = front
        landmarker = buildLandmarker()
        val executor = Executors.newSingleThreadExecutor()
        analysisExecutor = executor

        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            val cameraProvider = future.get()
            provider = cameraProvider

            val analysis = ImageAnalysis.Builder()
                // Only the newest frame matters; a backlog would make the
                // gesture lag behind the hand.
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
                .build()
            analysis.setAnalyzer(executor, ::analyze)

            val previewUseCase = Preview.Builder().build()
            preview = previewUseCase

            val selector = if (front) {
                CameraSelector.DEFAULT_FRONT_CAMERA
            } else {
                CameraSelector.DEFAULT_BACK_CAMERA
            }

            cameraProvider.unbindAll()
            cameraProvider.bindToLifecycle(owner, selector, previewUseCase, analysis)
        }, ContextCompat.getMainExecutor(context))
    }

    /** Attach the on-screen preview, or pass null when the UI goes away. */
    fun setSurfaceProvider(provider: Preview.SurfaceProvider?) {
        preview?.surfaceProvider = provider
    }

    private fun buildLandmarker(): HandLandmarker {
        val base = BaseOptions.builder()
            .setModelAssetPath(MODEL_ASSET)
            .setDelegate(Delegate.GPU)
            .build()
        val options = HandLandmarker.HandLandmarkerOptions.builder()
            .setBaseOptions(base)
            .setNumHands(1)
            // A closed fist has much lower hand-presence + tracking confidence
            // than an open palm (fingers curl and self-occlude). At 0.6 the fist
            // fell below and tracking dropped it — you'd see no landmarks at all.
            // Low presence/tracking keeps the fist held; detection stays higher
            // so we still re-acquire cleanly. The debounce/cooldown in the state
            // machine filters any extra jitter this lets through.
            .setMinHandDetectionConfidence(0.5f)
            .setMinHandPresenceConfidence(0.3f)
            .setMinTrackingConfidence(0.3f)
            .setRunningMode(RunningMode.LIVE_STREAM)
            .setResultListener { result, _ -> onResult(result) }
            .setErrorListener { /* a dropped frame is not worth killing the app */ }
            .build()
        return HandLandmarker.createFromOptions(context, options)
    }

    private fun analyze(proxy: ImageProxy) {
        try {
            val bitmap = proxy.toBitmap(isFront)
            landmarker?.detectAsync(
                BitmapImageBuilder(bitmap).build(),
                proxy.imageInfo.timestamp / 1_000_000, // ns -> ms, must be monotonic
            )
        } catch (e: Exception) {
            // A malformed frame must not take the camera loop down with it.
        } finally {
            proxy.close()
        }
    }

    private fun onResult(result: HandLandmarkerResult) {
        val hands = result.landmarks()
        val points = if (hands.isEmpty()) {
            emptyList()
        } else {
            hands[0].map { Point(it.x(), it.y()) }
        }
        val pose = if (points.isEmpty()) Pose.NO_HAND else classify(points)
        onFrame(points, pose, stateMachine.update(pose))
    }

    fun stop() {
        provider?.unbindAll()
        provider = null
        landmarker?.close()
        landmarker = null
        analysisExecutor?.shutdown()
        analysisExecutor = null
    }

    companion object {
        private const val MODEL_ASSET = "hand_landmarker.task"
    }
}

/**
 * RGBA_8888 frame -> upright, mirrored Bitmap.
 *
 * Two gotchas: rows are padded to a hardware-friendly stride, so the buffer is
 * wider than the image and has to be cropped; and the sensor is rotated
 * relative to the display, so the frame must be rotated before MediaPipe sees
 * it or the landmarks come back sideways.
 */
private fun ImageProxy.toBitmap(mirror: Boolean): Bitmap {
    val plane = planes[0]
    val padded = plane.rowStride / plane.pixelStride
    val full = Bitmap.createBitmap(padded, height, Bitmap.Config.ARGB_8888)
    full.copyPixelsFromBuffer(plane.buffer)

    val matrix = Matrix().apply {
        postRotate(imageInfo.rotationDegrees.toFloat())
        if (mirror) postScale(-1f, 1f)
    }
    val out = Bitmap.createBitmap(full, 0, 0, width, height, matrix, true)
    if (out != full) full.recycle()
    return out
}
