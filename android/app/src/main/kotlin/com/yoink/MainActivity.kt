package com.yoink

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.provider.Settings
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.yoink.gesture.GestureEvent
import com.yoink.gesture.Point
import com.yoink.gesture.Pose
import com.yoink.net.Pairing
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine

/**
 * Milestone K2: a thin window onto [GestureService].
 *
 * The Activity no longer owns the camera — it starts the service, binds to it
 * for landmark updates, and lends it a surface to draw the preview on. Close
 * the Activity and the gesture loop carries on without it.
 *
 * Also carries the background-activity-launch spike from K1. That has already
 * passed on Android 14 (the framework logged BAL_ALLOW_SAW_PERMISSION at a 30s
 * delay), but it stays as a regression check: if an OS update ever revokes that
 * exemption, catching-while-backgrounded silently stops working, and this
 * button is how you'd find out.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var overlay: OverlayView
    private lateinit var status: TextView
    private lateinit var peers: TextView
    private lateinit var cameraToggle: Button

    private var service: GestureService? = null
    private var bound = false
    private var watching = false

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            val svc = (binder as GestureService.LocalBinder).service()
            service = svc
            bound = true
            svc.listener = object : GestureService.Listener {
                override fun onFrame(
                    landmarks: List<Point>,
                    pose: Pose,
                    event: GestureEvent?,
                ) {
                    runOnUiThread { overlay.update(landmarks, pose, event) }
                }

                override fun onStatus(msg: String, livePeers: Int) {
                    status.text = msg
                    updatePeers(livePeers)
                }
            }
            // Pairing needs a human, so the prompt only exists while the UI is
            // attached. With no Activity bound the mesh declines and backs off
            // rather than pairing silently.
            svc.mesh?.prompt = Pairing.PinPrompt { peerName -> askForPin(peerName) }
            watching = true
            updateToggle()
            status.text = svc.netStatus
            updatePeers(svc.mesh?.peers?.size ?: 0)
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
            bound = false
            // The service died on its own (e.g. OxygenOS killed it). Reflect
            // that in the toggle rather than leaving it stuck on "ON".
            watching = false
            updateToggle()
        }
    }

    /** The desktop prints the PIN on its console; the user types it here. */
    private suspend fun askForPin(peerName: String): String? =
        suspendCancellableCoroutine { cont ->
            runOnUiThread {
                val input = EditText(this).apply {
                    inputType = InputType.TYPE_CLASS_NUMBER
                    hint = "000000"
                }
                AlertDialog.Builder(this)
                    .setTitle(getString(R.string.pair_title, peerName))
                    .setMessage(getString(R.string.pair_message, peerName))
                    .setView(input)
                    .setCancelable(false)
                    .setPositiveButton(R.string.pair) { _, _ ->
                        if (cont.isActive) cont.resume(input.text.toString())
                    }
                    .setNegativeButton(android.R.string.cancel) { _, _ ->
                        if (cont.isActive) cont.resume(null)
                    }
                    .show()
            }
        }

    private val requestCamera = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) startAndBind() else status.text = getString(R.string.camera_denied)
    }

    private val requestNotifications = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* denied only hides the notification; the service still runs */ }

    // MediaProjection consent (K5). The dialog needs an Activity, so it is
    // obtained here while foreground; the SEND gesture fires later, possibly
    // backgrounded, and just uses the projection this set up.
    private val requestCapture = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == RESULT_OK && result.data != null) {
            val intent = Intent(this, ScreenCaptureService::class.java)
                .putExtra(ScreenCaptureService.EXTRA_CODE, result.resultCode)
                .putExtra(ScreenCaptureService.EXTRA_DATA, result.data)
            startForegroundService(intent)
            Toast.makeText(this, R.string.capture_on, Toast.LENGTH_SHORT).show()
        } else {
            // Declined: SEND falls back to clipboard-only, which is fine.
            Toast.makeText(this, R.string.capture_declined, Toast.LENGTH_LONG).show()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        overlay = findViewById(R.id.overlay)
        status = findViewById(R.id.status)
        peers = findViewById(R.id.peers)
        cameraToggle = findViewById(R.id.camera_toggle)

        cameraToggle.setOnClickListener { if (watching) stopWatching() else startAndBind() }
        findViewById<Button>(R.id.overlay_perm).setOnClickListener { requestOverlayPermission() }
        findViewById<Button>(R.id.enable_capture).setOnClickListener { requestScreenCapture() }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            requestNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED
        ) {
            startAndBind()
        } else {
            requestCamera.launch(Manifest.permission.CAMERA)
        }
    }

    /**
     * The service must be started while we are visible: CAMERA is a while-in-use
     * permission, so a `camera` foreground service started from the background
     * throws SecurityException.
     */
    private fun startAndBind() {
        val intent = Intent(this, GestureService::class.java)
        startForegroundService(intent)
        bindService(intent, connection, Context.BIND_AUTO_CREATE)
    }

    /** Show the system "allow capturing your screen?" dialog, once per session. */
    private fun requestScreenCapture() {
        if (ScreenCaptureService.instance?.isReady() == true) {
            Toast.makeText(this, R.string.capture_on, Toast.LENGTH_SHORT).show()
            return
        }
        val manager = getSystemService(MediaProjectionManager::class.java)
        requestCapture.launch(manager.createScreenCaptureIntent())
    }

    private fun stopWatching() {
        stopService(Intent(this, ScreenCaptureService::class.java))
        unbindIfNeeded()
        stopService(Intent(this, GestureService::class.java))
        watching = false
        updateToggle()
        updatePeers(0)
        overlay.update(emptyList(), com.yoink.gesture.Pose.NO_HAND, null)
        status.text = getString(R.string.stopped)
    }

    private fun updateToggle() {
        cameraToggle.setText(if (watching) R.string.camera_off else R.string.camera_on)
    }

    /** Green pill with a live peer count, or a muted "offline". */
    private fun updatePeers(live: Int) {
        if (live > 0) {
            peers.text = getString(R.string.peers_count, live)
            peers.setTextColor(ContextCompat.getColor(this, R.color.go))
        } else {
            peers.setText(R.string.offline)
            peers.setTextColor(ContextCompat.getColor(this, R.color.text_secondary))
        }
    }

    private fun unbindIfNeeded() {
        if (!bound) return
        service?.listener = null
        // Drop the prompt with the UI: a dialog needs a live Activity, so the
        // mesh must decline rather than await one that can never appear.
        service?.mesh?.prompt = null
        unbindService(connection)
        service = null
        bound = false
    }

    override fun onDestroy() {
        unbindIfNeeded()
        super.onDestroy()
    }

    // "Display over other apps" (SYSTEM_ALERT_WINDOW): the RECEIVE gesture and
    // the caught-pop can only launch from the background with this granted.

    private fun hasOverlayPermission() = Settings.canDrawOverlays(this)

    private fun requestOverlayPermission() {
        if (hasOverlayPermission()) {
            Toast.makeText(this, R.string.overlay_already, Toast.LENGTH_SHORT).show()
            return
        }
        startActivity(
            Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:$packageName"),
            )
        )
    }
}
