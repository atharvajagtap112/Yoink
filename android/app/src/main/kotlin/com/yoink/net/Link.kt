package com.yoink.net

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.channels.Channel
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * One open WebSocket, with inbound envelopes buffered into a queue.
 *
 * Python gets `await ws.recv()` for free. OkHttp gives us callbacks instead, so
 * frames land in an unlimited [Channel] and [next] reads from it. Buffering
 * matters: during the pairing handshake a reply can arrive before the next
 * `next()` call, and a callback with nowhere to put it would be dropped.
 */
class Link private constructor() {

    private val inbox = Channel<JSONObject>(Channel.UNLIMITED)
    private val opened = CompletableDeferred<Boolean>()
    private var socket: WebSocket? = null

    @Volatile
    var isClosed = false
        private set

    private val listener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            opened.complete(true)
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            Protocol.decode(text)?.let { inbox.trySend(it) }
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = fail()

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            // Fires for a refused dial too, so unblock anyone awaiting the open.
            opened.complete(false)
            fail()
        }
    }

    private fun fail() {
        if (isClosed) return
        isClosed = true
        // Closing the channel makes a suspended next() throw instead of hanging
        // forever on a socket that is never going to speak again.
        inbox.close()
    }

    /** Next envelope off the wire. Throws once the socket has closed. */
    suspend fun next(): JSONObject = inbox.receive()

    fun send(env: JSONObject) {
        if (!isClosed) socket?.send(Protocol.encode(env))
    }

    fun close() {
        socket?.close(1000, null)
        fail()
    }

    companion object {
        /**
         * Dial [url] and wait for the handshake. Returns null if the peer
         * refused, timed out, or the network is gone.
         */
        suspend fun connect(client: OkHttpClient, url: String): Link? {
            val link = Link()
            link.socket = client.newWebSocket(Request.Builder().url(url).build(), link.listener)
            return if (link.opened.await()) link else { link.close(); null }
        }

        /**
         * pingInterval is OkHttp's own keepalive and is *not* a substitute for
         * the app-level heartbeat in [Peer] — the desktop expects `heartbeat`
         * envelopes and will drop us without them. This just kills a
         * half-open TCP socket faster than the OS would.
         */
        fun newClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .pingInterval(5, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS) // a websocket is long-lived
            .build()
    }
}
