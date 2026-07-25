package com.yoink.yoink_mobile

import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.media.projection.MediaProjectionManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.util.DisplayMetrics
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * Holds a WifiManager.MulticastLock for as long as the app is in the
 * foreground, and bridges screen capture to Dart.
 *
 * The multicast lock: Android's WiFi stack filters out packets not addressed to
 * this device to save battery, and mDNS runs on the multicast address
 * 224.0.0.251. Without the lock, discovery finds nothing and reports no error —
 * it just stays empty forever, which is the most confusing way this can fail.
 *
 * Screen capture (milestone 7d): the projection itself lives in
 * ScreenCaptureService, because Android 14+ requires the foreground service to
 * be running before the projection may be created. This class only drives the
 * consent dialog and forwards capture requests.
 */
class MainActivity : FlutterActivity() {
    private var multicastLock: WifiManager.MulticastLock? = null

    private var captureService: ScreenCaptureService? = null
    private var pendingConsent: MethodChannel.Result? = null
    private var consentResultCode = 0
    private var consentData: Intent? = null

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val service = (binder as ScreenCaptureService.LocalBinder).service()
            captureService = service
            // We are here only after onStartCommand ran, so startForeground()
            // has completed and the projection is legal to create.
            val data = consentData
            val ok = data != null &&
                service.startProjection(
                    consentResultCode,
                    data,
                    screenWidth(),
                    screenHeight(),
                    screenDpi(),
                )
            finishConsent(ok)
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            captureService = null
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        multicastLock = wifi.createMulticastLock("yoink-mdns").apply {
            setReferenceCounted(true)
            acquire()
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "isReady" -> result.success(captureService?.isReady() == true)
                    "requestConsent" -> requestConsent(result)
                    "capture" -> capture(result)
                    else -> result.notImplemented()
                }
            }
    }

    /** Show the system "allow capturing your screen?" dialog, once per session. */
    private fun requestConsent(result: MethodChannel.Result) {
        if (captureService?.isReady() == true) {
            result.success(true)
            return
        }
        if (pendingConsent != null) {
            result.success(false) // a dialog is already up; don't stack them
            return
        }
        pendingConsent = result
        val manager = getSystemService(MediaProjectionManager::class.java)
        @Suppress("DEPRECATION")
        startActivityForResult(manager.createScreenCaptureIntent(), CONSENT_REQUEST)
    }

    @Suppress("DEPRECATION")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != CONSENT_REQUEST) {
            super.onActivityResult(requestCode, resultCode, data)
            return
        }
        if (resultCode != Activity.RESULT_OK || data == null) {
            finishConsent(false) // user declined; Dart falls back to clipboard only
            return
        }
        consentResultCode = resultCode
        consentData = data

        val intent = Intent(this, ScreenCaptureService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
        // The projection is created in onServiceConnected, not here — see above.
        bindService(intent, connection, Context.BIND_AUTO_CREATE)
    }

    private fun finishConsent(granted: Boolean) {
        pendingConsent?.success(granted)
        pendingConsent = null
    }

    private fun capture(result: MethodChannel.Result) {
        val service = captureService
        if (service == null || !service.isReady()) {
            result.success(null)
            return
        }
        // capture() blocks waiting for a frame, so keep it off the main thread.
        // MethodChannel results must be delivered on the main thread.
        Thread {
            val bytes = service.capture()
            runOnUiThread { result.success(bytes) }
        }.start()
    }

    private fun metrics(): DisplayMetrics {
        val dm = DisplayMetrics()
        @Suppress("DEPRECATION")
        windowManager.defaultDisplay.getRealMetrics(dm)
        return dm
    }

    private fun screenWidth() = metrics().widthPixels
    private fun screenHeight() = metrics().heightPixels
    private fun screenDpi() = metrics().densityDpi

    override fun onDestroy() {
        if (captureService != null) {
            unbindService(connection)
            captureService = null
        }
        stopService(Intent(this, ScreenCaptureService::class.java))
        multicastLock?.let { if (it.isHeld) it.release() }
        multicastLock = null
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL = "yoink/screencap"
        private const val CONSENT_REQUEST = 4242
    }
}
