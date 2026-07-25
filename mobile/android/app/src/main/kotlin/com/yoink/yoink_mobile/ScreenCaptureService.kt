package com.yoink.yoink_mobile

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
import android.os.Binder
import android.os.Build
import android.os.IBinder
import java.io.ByteArrayOutputStream

/**
 * Owns the MediaProjection and turns it into single PNG frames on demand.
 *
 * Why a service at all: since Android 14, MediaProjection refuses to start
 * unless a foreground service with type `mediaProjection` is ALREADY running.
 * Starting the service and creating the projection from the Activity in one go
 * loses that race — onStartCommand hasn't run yet — so the projection lives
 * here, created only after startForeground() has completed.
 *
 * The VirtualDisplay is created once and left running for the app session.
 * Android 14+ makes each consent token single-use, so tearing the display down
 * after every grab would mean a new "allow capturing?" dialog on every gesture.
 * One long-lived session is what makes "consent once, grab many times" possible.
 *
 * ponytail: the display streams frames continuously while the app is open, so
 * the screen-cast icon stays in the status bar and it costs some battery. The
 * alternative is a consent dialog per gesture, which would wreck the feel.
 */
class ScreenCaptureService : Service() {

    inner class LocalBinder : Binder() {
        fun service(): ScreenCaptureService = this@ScreenCaptureService
    }

    private val binder = LocalBinder()
    private var projection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var width = 0
    private var height = 0

    private val projectionCallback = object : MediaProjection.Callback() {
        // Required on Android 14+; also fires if the user revokes capture from
        // the status bar, which must not leave us holding a dead display.
        override fun onStop() {
            teardown()
        }
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForegroundNotification()
        return START_NOT_STICKY
    }

    private fun startForegroundNotification() {
        val channelId = "yoink_capture"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel(
                    channelId,
                    "Yoink screen capture",
                    NotificationManager.IMPORTANCE_LOW,
                )
            )
        }
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, channelId)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        val notification = builder
            .setContentTitle("Yoink")
            .setContentText("Ready to grab your screen")
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    /** Create the projection. Safe to call only once startForeground() has run. */
    fun startProjection(resultCode: Int, data: Intent, w: Int, h: Int, dpi: Int): Boolean {
        if (projection != null) return true
        width = w
        height = h
        val manager = getSystemService(MediaProjectionManager::class.java)
        val mp = try {
            manager.getMediaProjection(resultCode, data)
        } catch (e: SecurityException) {
            // Android 14+ throws here if the foreground service isn't up yet.
            null
        } ?: return false

        mp.registerCallback(projectionCallback, null)
        val reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        virtualDisplay = mp.createVirtualDisplay(
            "yoink-capture",
            width,
            height,
            dpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            reader.surface,
            null,
            null,
        )
        imageReader = reader
        projection = mp
        return true
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
            val pixelStride = plane.pixelStride
            val rowStride = plane.rowStride
            // Rows are padded to a hardware-friendly stride, so the buffer is
            // wider than the screen. Copy the padded bitmap, then crop it.
            val padded = rowStride / pixelStride
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
        super.onDestroy()
    }

    companion object {
        private const val NOTIFICATION_ID = 1
    }
}
