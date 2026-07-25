package com.yoink

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.IBinder
import java.io.ByteArrayOutputStream

/**
 * Owns the MediaProjection and turns it into single PNG frames on demand.
 *
 * Ported almost verbatim from the Flutter 7d spike (DESIGN.md section 3a). A
 * separate foreground service from [GestureService] because its type is
 * `mediaProjection`, not `camera`.
 *
 * Why a service at all: since Android 14, MediaProjection refuses to start
 * unless a foreground service of type `mediaProjection` is ALREADY running. So
 * the projection is created in onStartCommand, only after startForeground() has
 * returned — never inline from the Activity.
 *
 * The VirtualDisplay is created once and left running for the app session.
 * Android 14+ makes each consent token single-use, so tearing the display down
 * after every grab would mean a new "allow capturing?" dialog on every gesture.
 * One long-lived session is what makes "consent once, grab many times" work.
 *
 * ponytail: a process-wide `instance` handle instead of binding both services
 * together. There is only ever one of each foreground service, and the grab
 * path (in GestureService) just needs "capture if a projection exists". A bound
 * connection between two services would be more moving parts for the same
 * result.
 */
class ScreenCaptureService : Service() {

    private var projection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var width = 0
    private var height = 0

    private val projectionCallback = object : MediaProjection.Callback() {
        // Required on Android 14+; also fires if the user revokes capture from
        // the status bar, which must not leave us holding a dead display.
        override fun onStop() = teardown()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        startForegroundNotification()

        // startForeground() above has returned, so the projection is now legal.
        val resultCode = intent?.getIntExtra(EXTRA_CODE, 0) ?: 0
        @Suppress("DEPRECATION")
        val data = intent?.getParcelableExtra<Intent>(EXTRA_DATA)
        if (data != null) startProjection(resultCode, data)
        return START_NOT_STICKY
    }

    private fun startForegroundNotification() {
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "Yoink screen capture",
                NotificationManager.IMPORTANCE_LOW,
            )
        )
        val notification = Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Yoink")
            .setContentText("Ready to grab your screen")
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .build()
        startForeground(
            NOTIFICATION_ID,
            notification,
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION,
        )
    }

    private fun startProjection(resultCode: Int, data: Intent) {
        if (projection != null) return
        val metrics = resources.displayMetrics
        width = metrics.widthPixels
        height = metrics.heightPixels

        val manager = getSystemService(MediaProjectionManager::class.java)
        val mp = try {
            manager.getMediaProjection(resultCode, data)
        } catch (e: SecurityException) {
            null // Android 14+ throws here if the FGS isn't up yet
        } ?: return

        mp.registerCallback(projectionCallback, null)
        val reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        virtualDisplay = mp.createVirtualDisplay(
            "yoink-capture",
            width,
            height,
            metrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            reader.surface,
            null,
            null,
        )
        imageReader = reader
        projection = mp
    }

    fun isReady(): Boolean = projection != null && imageReader != null

    /** Grab the newest frame as PNG bytes. Blocking — call off the main thread. */
    fun capture(): ByteArray? {
        val reader = imageReader ?: return null
        // The display streams continuously, but right after startup there may be
        // no frame yet. Give it a few tries rather than returning empty.
        var image = reader.acquireLatestImage()
        var tries = 0
        while (image == null && tries < 20) {
            Thread.sleep(50)
            image = reader.acquireLatestImage()
            tries++
        }
        if (image == null) return null

        return try {
            val plane = image.planes[0]
            // Rows are padded to a hardware-friendly stride, so the buffer is
            // wider than the screen. Copy the padded bitmap, then crop it.
            val padded = plane.rowStride / plane.pixelStride
            val bitmap = Bitmap.createBitmap(padded, height, Bitmap.Config.ARGB_8888)
            bitmap.copyPixelsFromBuffer(plane.buffer)
            val cropped = Bitmap.createBitmap(bitmap, 0, 0, width, height)
            val out = ByteArrayOutputStream()
            cropped.compress(Bitmap.CompressFormat.PNG, 100, out)
            if (cropped != bitmap) cropped.recycle()
            bitmap.recycle()
            out.toByteArray()
        } catch (e: Exception) {
            null
        } finally {
            image.close()
        }
    }

    private fun teardown() {
        virtualDisplay?.release()
        virtualDisplay = null
        imageReader?.close()
        imageReader = null
        projection = null
    }

    override fun onDestroy() {
        projection?.unregisterCallback(projectionCallback)
        projection?.stop()
        teardown()
        if (instance === this) instance = null
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL_ID = "yoink_capture"
        private const val NOTIFICATION_ID = 1
        const val ACTION_STOP = "com.yoink.STOP_CAPTURE"
        const val EXTRA_CODE = "code"
        const val EXTRA_DATA = "data"

        /** The single live instance, or null if screen capture isn't set up. */
        @Volatile
        var instance: ScreenCaptureService? = null
            private set
    }
}
