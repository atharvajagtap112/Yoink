package com.yoink.net

import android.content.Context
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.withTimeout
import org.json.JSONObject

/**
 * PIN pairing: the trust gate that must pass before any payload is accepted.
 *
 * Port of the *dialer* half of daemon/net/pairing.py. The phone is client-only
 * (CLAUDE.md section 3), so it is always the dialing side and never the one
 * that shows a PIN — the desktop prints the PIN on its console and you type it
 * here. Only the guess crosses the wire, so sniffing the traffic doesn't reveal
 * it.
 *
 * Handshake, dialer's view:
 *     me   -> hello   (data = my device_id, sender = my name)
 *     peer -> hello
 *     me   -> pair    type=known|new   ("do I already have you stored?")
 *     peer -> pair    type=ok          -> already trusted, done
 *                or   type=request     -> peer is showing a PIN
 *     me   -> pair    type=confirm, data=the pin the user typed
 *     peer -> pair    type=ok | deny
 */
object Pairing {

    /** Generous: a human is reading a PIN off a laptop screen and typing it. */
    private const val TIMEOUT_MS = 120_000L

    /** A trusted peer's identity, returned once the handshake passes. */
    data class Result(val deviceId: String, val name: String)

    /**
     * Asks the user for the PIN the desktop is displaying. Returns null to
     * decline — which is also what happens when no UI is attached, since
     * pairing is a deliberate one-time action taken with the app open.
     */
    fun interface PinPrompt {
        suspend fun ask(peerName: String): String?
    }

    /**
     * The peers we've already trusted, persisted so pairing is one-time.
     * Mirrors PairedStore in daemon/net/pairing.py.
     */
    class Store(context: Context) {
        private val prefs = context.getSharedPreferences("yoink.paired", Context.MODE_PRIVATE)

        fun isPaired(deviceId: String): Boolean = prefs.contains(deviceId)

        fun nameOf(deviceId: String): String? = prefs.getString(deviceId, null)

        fun add(deviceId: String, name: String) {
            prefs.edit().putString(deviceId, name).apply()
        }

        fun names(): List<String> = prefs.all.values.mapNotNull { it as? String }

        fun forgetAll() = prefs.edit().clear().apply()
    }

    private fun pairMsg(step: String, sender: String, pin: String? = null) =
        Protocol.envelope("pair", type = step, data = pin, sender = sender)

    /**
     * Read until a message of [kind] arrives.
     *
     * Payloads that show up before pairing is done are refused here — this
     * rejection is the whole reason the feature exists.
     */
    private suspend fun recvKind(
        link: Link,
        kind: String,
        log: (String) -> Unit,
    ): JSONObject = withTimeout(TIMEOUT_MS) {
        while (true) {
            val env = link.next()
            when (env.stringOrNull("kind")) {
                "payload" -> log("REJECTED payload from unpaired peer ${env.stringOrNull("sender")}")
                kind -> return@withTimeout env
                // anything else (a stray heartbeat) is ignored mid-handshake
            }
        }
        @Suppress("UNREACHABLE_CODE") error("unreachable")
    }

    /**
     * Run the handshake on a fresh connection. Returns the peer if it may join
     * the mesh, or null if the connection must be dropped.
     */
    suspend fun handshake(
        link: Link,
        meName: String,
        meId: String,
        store: Store,
        log: (String) -> Unit,
        prompt: PinPrompt?,
    ): Result? = try {
        link.send(Protocol.envelope("hello", data = meId, sender = meName))
        val hello = recvKind(link, "hello", log)

        val peerId = hello.stringOrNull("data")
        val peerName = hello.stringOrNull("sender") ?: "unknown"

        if (peerId.isNullOrEmpty() || peerId == meId) {
            null // nameless, or ourselves
        } else {
            link.send(pairMsg(if (store.isPaired(peerId)) "known" else "new", meName))
            when (recvKind(link, "pair", log).stringOrNull("type")) {
                "ok" -> Result(peerId, peerName) // both sides already trusted
                "request" -> confirmPin(link, peerId, peerName, meName, store, log, prompt)
                else -> null
            }
        }
    } catch (e: TimeoutCancellationException) {
        log("pairing timed out")
        null
    } catch (e: Exception) {
        null // socket died mid-handshake; the dial loop will retry
    }

    private suspend fun confirmPin(
        link: Link,
        peerId: String,
        peerName: String,
        meName: String,
        store: Store,
        log: (String) -> Unit,
        prompt: PinPrompt?,
    ): Result? {
        if (prompt == null) {
            log("PAIR REQUEST from $peerName — open the Yoink app to enter the PIN")
            return null
        }
        log("PAIR REQUEST from $peerName — check the PIN on its console")
        val pin = prompt.ask(peerName)
        if (pin == null) {
            log("pairing cancelled")
            return null
        }
        link.send(pairMsg("confirm", meName, pin.trim()))

        if (recvKind(link, "pair", log).stringOrNull("type") != "ok") {
            log("PAIR DENIED by $peerName (wrong PIN)")
            return null
        }
        store.add(peerId, peerName)
        log("PAIRED with $peerName (saved; won't ask again)")
        return Result(peerId, peerName)
    }
}
