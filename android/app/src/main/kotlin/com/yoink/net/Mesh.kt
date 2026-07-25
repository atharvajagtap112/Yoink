package com.yoink.net

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap

/**
 * The phone's half of the mesh: dial discovered desktops, pair, receive.
 *
 * Port of daemon/net/mesh.py with one deliberate difference: **the phone is
 * client-only** (DESIGN.md section 3). It never listens and never advertises,
 * so the "lower device_id dials" dedupe rule doesn't apply — the desktop can't
 * discover us, so it can never dial us, so there is no second socket to dedupe.
 * We always dial, and the one full-duplex socket carries both directions.
 *
 * Reconnect: unlike the Python version, a dial loop is *not* torn down when
 * mDNS says the service went away. On a phone, "service lost" usually means
 * WiFi blipped, not that the desktop is gone, and NSD can be slow to
 * re-announce. Retrying against the last known address is what makes "drop
 * WiFi, bring it back" rejoin on its own.
 *
 * ponytail: retry-forever against the last address; fine for a handful of
 * desktops. If a desktop's LAN IP changes while it's down, discovery updates
 * the target on its next announcement.
 */
class Mesh(
    private val scope: CoroutineScope,
    private val name: String,
    private val deviceId: String,
    private val store: Pairing.Store,
    private val onReceive: (JSONObject) -> Unit,
    private val onStatus: (String) -> Unit,
) {
    private data class Target(val host: String, val port: Int)

    private val client = Link.newClient()
    private val targets = ConcurrentHashMap<String, Target>()
    private val dialers = ConcurrentHashMap<String, Job>()

    /** device_id -> Peer (paired and live only). */
    val peers = ConcurrentHashMap<String, Peer>()

    /**
     * One PIN at a time: the dialog is a single shared resource, and pairing
     * with two desktops at once would land your answer in the wrong handshake.
     */
    private val promptLock = Mutex()

    /** Set by the UI when it's attached; null means "decline and back off". */
    @Volatile
    var prompt: Pairing.PinPrompt? = null

    // --- discovery callbacks ---

    fun peerUp(peerId: String, host: String, port: Int) {
        val target = Target(host, port)
        val known = targets.put(peerId, target)
        if (dialers.containsKey(peerId)) {
            if (known != target) onStatus("${peerId.take(8)} moved to $host:$port")
            return // already dialing; the loop picks up the new address
        }
        onStatus("discovered ${peerId.take(8)} at $host:$port — dialing")
        dialers[peerId] = scope.launch { dialLoop(peerId) }
    }

    fun peerDown(peerId: String) {
        // Keep the target and the dial loop (see the reconnect note above).
        onStatus("peer gone from discovery: ${peerId.take(8)} — still retrying")
    }

    // --- connection lifecycle ---

    private suspend fun dialLoop(peerId: String) {
        while (scope.isActive) {
            val target = targets[peerId]
            if (target == null || peers.containsKey(peerId)) {
                delay(RETRY_MS)
                continue
            }
            var paired = true
            try {
                val link = Link.connect(client, "ws://${target.host}:${target.port}")
                if (link != null) paired = session(link)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // refused / timed out / WiFi gone: just retry
            }
            delay(if (paired) RETRY_MS else PAIR_RETRY_MS)
        }
    }

    /**
     * Pair, then serve the connection until it drops. Returns false only when
     * pairing itself failed, so the dial loop can back off instead of
     * re-prompting for a PIN every second.
     */
    private suspend fun session(link: Link): Boolean {
        val result = promptLock.withLock {
            Pairing.handshake(link, name, deviceId, store, onStatus, prompt)
        }
        if (result == null) {
            link.close()
            return false
        }
        if (peers.containsKey(result.deviceId)) {
            onStatus("duplicate link to ${result.name} — dropping the extra")
            link.close()
            return true
        }

        val peer = Peer(link, result.deviceId, result.name, name, onReceive, onStatus)
        peers[result.deviceId] = peer
        onStatus("peer UP: ${result.name} (${peers.size} live)")
        try {
            peer.run()
        } finally {
            peers.remove(result.deviceId)
            onStatus("peer DOWN: ${result.name} (${peers.size} live)")
        }
        return true
    }

    /** Fan a payload out to every paired peer. Unused until K5. */
    fun broadcast(env: JSONObject) {
        val live = peers.values.toList()
        if (live.isEmpty()) {
            onStatus("broadcast: no paired peers yet — nothing sent")
            return
        }
        live.forEach { it.send(env) }
        onStatus("broadcast -> ${live.size} peers")
    }

    fun stop() {
        dialers.values.forEach { it.cancel() }
        dialers.clear()
        peers.values.forEach { it.link.close() }
        peers.clear()
    }

    companion object {
        private const val RETRY_MS = 1_000L

        /** After a failed pairing, so a denial can't spam the PIN prompt. */
        private const val PAIR_RETRY_MS = 30_000L
    }
}
