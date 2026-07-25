package com.yoink

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.IBinder
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.yoink.grab.Share
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject

/**
 * Handles "Share → Yoink" (milestone K6). No UI of its own — it parses the
 * shared content, throws it to the mesh, toasts the result and finishes.
 *
 * Broadcasting needs a live paired peer, which lives in the mesh inside
 * [GestureService]. So this ensures that service is up, waits briefly for a
 * peer to connect, then broadcasts.
 *
 * ponytail: this reuses the camera service to reach the mesh, which means a
 * share turns the camera on as a side effect. Splitting networking into its own
 * service would avoid that, but it's a real refactor of K3 for a cosmetic win;
 * the persistent notification and the on/off toggle already let you stop it.
 * Upgrade path: a standalone NetworkService if share-without-camera matters.
 */
class ShareActivity : ComponentActivity() {

    private var service: GestureService? = null
    private var envelopes: List<JSONObject> = emptyList()

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            service = (binder as GestureService.LocalBinder).service()
            broadcastWhenReady()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        envelopes = Share.fromSendIntent(this, intent, Identity.load(this).name, ::log)
        if (envelopes.isEmpty()) {
            toast("Yoink: nothing to share")
            finish()
            return
        }

        // The mesh rides in the camera service, and starting a `camera`
        // foreground service needs the CAMERA permission. Without it, tell the
        // user to open Yoink once rather than crashing on the service start.
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            != PackageManager.PERMISSION_GRANTED
        ) {
            toast("Open Yoink once and grant camera, then share again")
            finish()
            return
        }

        val svc = Intent(this, GestureService::class.java)
        startForegroundService(svc)
        bindService(svc, connection, Context.BIND_AUTO_CREATE)
    }

    private fun broadcastWhenReady() {
        lifecycleScope.launch {
            val mesh = service?.mesh
            // Discovery + dial + (first time) pairing can take a moment; wait,
            // but not forever. If nothing connects, say so plainly.
            val connected = withTimeoutOrNull(PEER_WAIT_MS) {
                while (mesh?.peers?.isEmpty() != false) delay(200)
                true
            } == true

            if (connected) {
                envelopes.forEach { mesh?.broadcast(it) }
                toast("Yoink: sent ${envelopes.size} item(s)")
            } else {
                toast("Yoink: no paired desktop connected")
            }
            finish()
        }
    }

    override fun onDestroy() {
        try {
            unbindService(connection)
        } catch (e: IllegalArgumentException) {
            // never bound (early finish); nothing to unwind
        }
        super.onDestroy()
    }

    private fun log(msg: String) = android.util.Log.i("Yoink", "[share] $msg")

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()

    private companion object {
        const val PEER_WAIT_MS = 8_000L
    }
}
