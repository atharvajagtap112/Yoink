package com.yoink

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Binder
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Log
import android.widget.Toast
import androidx.camera.core.Preview
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import com.yoink.gesture.GestureEvent
import com.yoink.gesture.HandTracker
import com.yoink.gesture.Point
import com.yoink.gesture.Pose
import com.yoink.grab.Grab
import com.yoink.net.Discovery
import com.yoink.net.Mesh
import com.yoink.net.Pairing
import com.yoink.net.Protocol
import com.yoink.receive.Dispatch
import com.yoink.receive.Pop
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Milestone K2: the camera lives here, not in the Activity.
 *
 * This is the whole reason the client moved off Flutter. CameraX binds its use
 * cases to a LifecycleOwner; a LifecycleService is one, so the camera keeps
 * streaming after the Activity is gone and gestures keep firing while you're in
 * Chrome or on the homescreen.
 *
 * Android requires a `camera` foreground service type for this, plus the
 * CAMERA runtime permission, and the service must be *started while the app is
 * visible* — CAMERA is a while-in-use permission, so starting it from the
 * background throws SecurityException. MainActivity starts it, which satisfies
 * that.
 *
 * ponytail: the camera and MediaPipe inference run continuously while this
 * service is up. That is genuinely expensive — it is the cost of the feature,
 * not an oversight. The notification's Stop action is the off switch.
 */
class GestureService : LifecycleService() {

    /** What the UI (when there is one) wants to hear about. */
    interface Listener {
        fun onFrame(landmarks: List<Point>, pose: Pose, event: GestureEvent?)

        /** Networking progress, for the status bar. Always on the main thread. */
        fun onStatus(msg: String, livePeers: Int) {}
    }

    inner class LocalBinder : Binder() {
        fun service(): GestureService = this@GestureService
    }

    private val binder = LocalBinder()
    private var tracker: HandTracker? = null
    private val main = Handler(Looper.getMainLooper())

    private var multicastLock: WifiManager.MulticastLock? = null
    private var discovery: Discovery? = null

    /** Null until the network is up; the gesture loop doesn't depend on it. */
    var mesh: Mesh? = null
        private set

    /** Null whenever no Activity is bound; the gesture loop doesn't care. */
    var listener: Listener? = null

    /** Last networking line, so a freshly-bound Activity can show something. */
    @Volatile
    var netStatus: String = "starting..."
        private set

    /**
     * Only the last received payload is kept (DESIGN.md section 2): decoded and
     * saved on arrival, opened on the RECEIVE gesture or a tap on the pop.
     */
    @Volatile
    private var lastCaught: Dispatch.Caught? = null

    private val pop by lazy { Pop(this) }

    /** The `sender` we stamp on what we send. Set once the network is up. */
    private var myName: String = "yoink-phone"

    var lastPose: Pose = Pose.NO_HAND
        private set

    override fun onCreate() {
        super.onCreate()
        startForegroundNotification()

        tracker = HandTracker(this) { points, pose, event ->
            lastPose = pose
            listener?.onFrame(points, pose, event)
            if (event != null) onGesture(event)
        }.also {
            // `this` — the service — is the lifecycle owner. That single choice
            // is what makes background gestures work.
            it.start(this, front = true)
        }

        startNetwork()
    }

    private fun startNetwork() {
        // Android's WiFi stack filters packets not addressed to this device, and
        // mDNS runs on 224.0.0.251. NsdManager does its own multicast in the
        // system process so this is probably redundant — but the failure mode is
        // "discovery silently finds nothing", so the lock is cheap insurance.
        multicastLock = (getSystemService(WIFI_SERVICE) as WifiManager)
            .createMulticastLock("yoink-mdns").apply {
                setReferenceCounted(true)
                acquire()
            }

        val me = Identity.load(this)
        myName = me.name
        val store = Pairing.Store(this)
        log("I am ${me.name} (id ${me.deviceId.take(8)})")
        if (store.names().isNotEmpty()) log("already paired with: ${store.names()}")

        val m = Mesh(
            scope = lifecycleScope,
            name = me.name,
            deviceId = me.deviceId,
            store = store,
            onReceive = ::onPayload,
            onStatus = ::log,
        )
        mesh = m

        discovery = Discovery(
            context = this,
            onPeerUp = m::peerUp,
            onPeerDown = m::peerDown,
            log = ::log,
        ).also { it.start() }
    }

    private fun log(msg: String) {
        Log.i(TAG, "[net] $msg")
        netStatus = msg
        main.post { listener?.onStatus(msg, mesh?.peers?.size ?: 0) }
    }

    /**
     * A payload landed over the network. Decode and save it now so the pop can
     * show a real thumbnail, but don't open anything — that waits for the
     * RECEIVE gesture or a tap, so nothing launches an app behind your back.
     */
    private fun onPayload(env: JSONObject) {
        val caught = Dispatch.prepare(this, env, ::log) ?: return
        lastCaught = caught
        main.post {
            pop.show(caught) { openLast() }
            listener?.onStatus("caught: ${caught.label}", mesh?.peers?.size ?: 0)
        }
    }

    /**
     * Open the last caught payload by type. Fired by the RECEIVE gesture and by
     * a tap on the pop. On the homescreen this relies on SYSTEM_ALERT_WINDOW to
     * launch at all (DESIGN.md section 3a).
     */
    private fun openLast() {
        val caught = lastCaught
        if (caught == null) {
            main.post { Toast.makeText(this, "Yoink: nothing caught yet", Toast.LENGTH_SHORT).show() }
            return
        }
        val what = Dispatch.open(this, caught, ::log)
        main.post {
            Toast.makeText(this, "Yoink: $what", Toast.LENGTH_SHORT).show()
            listener?.onStatus(what, mesh?.peers?.size ?: 0)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent): IBinder {
        super.onBind(intent)
        return binder
    }

    /**
     * Swiping Yoink out of the recents list stops the watch entirely — the
     * camera, the mesh, everything. Without this a foreground service keeps
     * running after the task is gone, which is both a battery surprise and not
     * what "I closed the app" should mean. Deliberately kept, and also stops the
     * screen-capture service so no orphan projection survives.
     */
    override fun onTaskRemoved(rootIntent: Intent?) {
        stopService(Intent(this, ScreenCaptureService::class.java))
        stopSelf()
        super.onTaskRemoved(rootIntent)
    }

    private fun onGesture(event: GestureEvent) {
        Log.i(TAG, "gesture: $event (app may be backgrounded)")
        when (event) {
            GestureEvent.RECEIVE -> openLast() // open hand = catch what's waiting
            GestureEvent.SEND -> doSend() // fist = grab and throw
        }
    }

    /** Grab something and broadcast it to every paired peer. */
    private fun doSend() {
        val m = mesh
        if (m == null || m.peers.isEmpty()) {
            main.post { Toast.makeText(this, "Yoink: no paired peers", Toast.LENGTH_SHORT).show() }
            return
        }
        // Grab.grab() reads the clipboard and may block on a screenshot frame,
        // so keep it off the main thread; broadcast is non-blocking.
        lifecycleScope.launch(Dispatchers.IO) {
            val env = Grab.grab(this@GestureService, myName, ::log)
            val msg = if (env == null) {
                "nothing to grab"
            } else {
                m.broadcast(env)
                "sent: ${Protocol.describe(env)}"
            }
            main.post { Toast.makeText(this@GestureService, "Yoink: $msg", Toast.LENGTH_SHORT).show() }
        }
    }

    fun setSurfaceProvider(provider: Preview.SurfaceProvider?) {
        tracker?.setSurfaceProvider(provider)
    }

    private fun startForegroundNotification() {
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "Yoink gesture watch",
                NotificationManager.IMPORTANCE_LOW,
            )
        )

        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this,
            1,
            Intent(this, GestureService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )

        val notification: Notification = Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Yoink is watching")
            .setContentText("Fist = send, open hand = catch")
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentIntent(open)
            .addAction(
                Notification.Action.Builder(null, "Stop", stop).build()
            )
            .setOngoing(true)
            .build()

        startForeground(
            NOTIFICATION_ID,
            notification,
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA,
        )
    }

    override fun onDestroy() {
        listener = null
        discovery?.stop()
        discovery = null
        mesh?.stop()
        mesh = null
        multicastLock?.let { if (it.isHeld) it.release() }
        multicastLock = null
        pop.hide()
        tracker?.stop()
        tracker = null
        super.onDestroy()
    }

    companion object {
        private const val TAG = "Yoink"
        private const val CHANNEL_ID = "yoink_gesture"
        private const val NOTIFICATION_ID = 42
        const val ACTION_STOP = "com.yoink.STOP"
    }
}
