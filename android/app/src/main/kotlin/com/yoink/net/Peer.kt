package com.yoink.net

import android.os.SystemClock
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * One paired WebSocket connection, with its own heartbeat lifecycle.
 *
 * Port of daemon/net/peer.py — same intervals, so both ends agree on when a
 * link is dead. A Peer only exists once pairing has passed, so anything holding
 * one is already trusted.
 *
 * Heartbeats: send a `heartbeat` envelope every [HEARTBEAT_MS]; any inbound
 * message refreshes "last heard". Silent for [DEAD_MS] (~3 missed beats) and
 * the link is declared dead and closed. A peer whose process is killed closes
 * the socket cleanly and is noticed immediately — the heartbeat is for the
 * ungraceful case (frozen peer, dropped WiFi) where the socket is left
 * half-open. On a phone that is the common case, not the rare one.
 */
class Peer(
    val link: Link,
    val deviceId: String,
    val name: String,
    private val meName: String,
    private val onReceive: (JSONObject) -> Unit,
    private val onStatus: (String) -> Unit,
) {
    private var lastHeard = SystemClock.elapsedRealtime()

    fun send(env: JSONObject) = link.send(env)

    /** Serve this connection until it closes. Returns when the peer is gone. */
    suspend fun run() = coroutineScope {
        val heartbeat = launch {
            while (isActive) {
                delay(HEARTBEAT_MS)
                if (SystemClock.elapsedRealtime() - lastHeard > DEAD_MS) {
                    onStatus("heartbeat lost — dropping $name")
                    link.close()
                    return@launch
                }
                link.send(Protocol.envelope("heartbeat", sender = meName))
            }
        }

        try {
            while (true) {
                val env = link.next()
                lastHeard = SystemClock.elapsedRealtime()
                if (env.stringOrNull("kind") == "payload") onReceive(env)
                // heartbeats need no handling; refreshing lastHeard is the point
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // channel closed: normal end of life for this link
        } finally {
            heartbeat.cancel()
            link.close()
        }
    }

    companion object {
        const val HEARTBEAT_MS = 3_000L
        const val DEAD_MS = 10_000L
    }
}
