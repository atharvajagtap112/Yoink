package com.yoink.net

import android.content.Context
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.withTimeout
import org.json.JSONObject

/**
 * PIN pairing + authenticated reconnect: the trust gate before any payload.
 *
 * Port of the *dialer* half of daemon/net/pairing.py. The phone is client-only
 * (DESIGN.md section 3), so it is always the dialing side: it never shows a PIN
 * and never issues the first challenge — the desktop displays the PIN and you
 * type it here.
 *
 * Two jobs, same as the desktop:
 *
 *  * **Pairing** (first meeting) — you type the PIN the desktop is showing. On
 *    success the desktop sends a random 32-byte **pairing key** which we store
 *    against its device_id.
 *  * **Authentication** (every reconnect after) — the desktop proves it holds
 *    that key over a nonce we generate, and we prove the same over its nonce.
 *    Mutual, so a rogue desktop can't impersonate your laptop to this phone and
 *    harvest whatever you throw.
 *
 * Before this existed, asserting a paired device_id was enough to be trusted —
 * and device_ids travel in the clear in every `hello`. Now the id only selects
 * which key to challenge with.
 *
 * There is deliberately **no fallback** to the old unauthenticated path: a peer
 * that answers our `known` with a bare `ok` is refused, because accepting it
 * would let an attacker downgrade simply by not implementing the new step. A
 * pairing made before this change has no stored key, so [Store.isPaired] reports
 * false and the PIN flow runs once more.
 *
 * Handshake, dialer's view:
 *     me   -> hello   (data = my device_id, sender = my name)
 *     peer -> hello
 *     me   -> pair    type=known, data=<my nonce>   if we hold a key for it
 *          -> pair    type=new                       otherwise
 *
 *     already paired:
 *       peer -> pair  type=challenge, data="<its nonce>:<its proof>"
 *       me            verify its proof, else drop the connection
 *       me   -> pair  type=proof, data=<my proof>
 *       peer -> pair  type=ok | deny
 *
 *     first meeting:
 *       peer -> pair  type=request        (it is showing a PIN)
 *       me   -> pair  type=confirm, data=<the PIN you typed>
 *       peer -> pair  type=ok, data=<pairing key> | type=deny
 */
object Pairing {

    /** Generous: a human is reading a PIN off a laptop screen and typing it. */
    private const val TIMEOUT_MS = 120_000L

    private const val NONCE_BYTES = 16
    private const val KEY_BYTES = 32

    /**
     * Domain separator, bound into every proof. Must stay identical to AUTH_TAG
     * in daemon/net/pairing.py — the two sides compute the same MAC or nothing
     * connects.
     */
    private const val AUTH_TAG = "yoink-auth-v1"

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
     * The peers we've trusted, and the key each proves itself with. Mirrors
     * PairedStore in daemon/net/pairing.py.
     *
     * Stored as JSON per device_id: {"name": ..., "secret": <hex>}. A bare
     * string is the pre-key format and reports as unpaired, so that peer runs
     * the PIN flow once more rather than being trusted on an assertion.
     */
    class Store(context: Context) {
        private val prefs = context.getSharedPreferences("yoink.paired", Context.MODE_PRIVATE)

        /** Trusted *and* able to prove it. A key-less record is not enough. */
        fun isPaired(deviceId: String): Boolean = secret(deviceId) != null

        /** The pairing key as raw bytes, or null if we can't authenticate them. */
        fun secret(deviceId: String): ByteArray? {
            val raw = prefs.getString(deviceId, null) ?: return null
            val hex = try {
                JSONObject(raw).optString("secret")
            } catch (e: Exception) {
                return null // legacy "name" record: no key, must pair again
            }
            val bytes = hexToBytes(hex) ?: return null
            return if (bytes.size == KEY_BYTES) bytes else null
        }

        fun nameOf(deviceId: String): String? {
            val raw = prefs.getString(deviceId, null) ?: return null
            return try {
                JSONObject(raw).optString("name").takeIf { it.isNotEmpty() }
            } catch (e: Exception) {
                raw // legacy record: the value *was* the name
            }
        }

        fun add(deviceId: String, name: String, secret: ByteArray) {
            val record = JSONObject()
                .put("name", name)
                .put("secret", toHex(secret))
            prefs.edit().putString(deviceId, record.toString()).apply()
        }

        fun names(): List<String> = prefs.all.keys.mapNotNull { nameOf(it) }

        fun forgetAll() = prefs.edit().clear().apply()
    }

    // --- proofs -------------------------------------------------------------

    /**
     * One side's proof that it holds [secret], over the other side's nonce.
     *
     * Both device ids are bound in, prover first, so the two directions of a
     * mutual exchange produce different MACs — otherwise a peer could reflect
     * our own challenge back at us and pass without holding anything.
     *
     * Byte-for-byte identical to proof() in daemon/net/pairing.py. Both test
     * suites pin the same known-answer vector; changing the message format here
     * changes that vector and both sides must move together.
     */
    fun proof(secret: ByteArray, nonce: String, proverId: String, verifierId: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(secret, "HmacSHA256"))
        val msg = "$AUTH_TAG|$nonce|$proverId|$verifierId".toByteArray(Charsets.UTF_8)
        return toHex(mac.doFinal(msg))
    }

    /** Constant-time, so a wrong proof leaks nothing through timing. */
    private fun sameProof(a: String, b: String): Boolean =
        MessageDigest.isEqual(a.toByteArray(Charsets.UTF_8), b.toByteArray(Charsets.UTF_8))

    private fun nonce(): String =
        toHex(ByteArray(NONCE_BYTES).also { SecureRandom().nextBytes(it) })

    fun toHex(bytes: ByteArray): String =
        bytes.joinToString("") { "%02x".format(it) }

    /** null for anything that isn't clean lowercase-or-upper hex. */
    fun hexToBytes(hex: String?): ByteArray? {
        if (hex.isNullOrEmpty() || hex.length % 2 != 0) return null
        return try {
            ByteArray(hex.length / 2) {
                hex.substring(it * 2, it * 2 + 2).toInt(16).toByte()
            }
        } catch (e: NumberFormatException) {
            null
        }
    }

    // --- messages -----------------------------------------------------------

    private fun pairMsg(step: String, sender: String, data: String? = null) =
        Protocol.envelope("pair", type = step, data = data, sender = sender)

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

    // --- handshake ----------------------------------------------------------

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
            val secret = store.secret(peerId)
            val myNonce = if (secret != null) nonce() else null
            link.send(pairMsg(if (secret != null) "known" else "new", meName, myNonce))

            val resp = recvKind(link, "pair", log)
            when (resp.stringOrNull("type")) {
                "challenge" ->
                    authenticate(link, resp, peerId, peerName, meName, meId, secret, myNonce, log)

                "request" ->
                    confirmPin(link, peerId, peerName, meName, store, log, prompt)

                // No `ok` branch: a peer we hold a key for must challenge us.
                // Waving a bare `ok` through is exactly the downgrade the class
                // docstring refuses to allow.
                "ok" -> {
                    log("REFUSED $peerName: it skipped authentication (old version, or someone pretending to be it)")
                    null
                }

                else -> null
            }
        }
    } catch (e: TimeoutCancellationException) {
        log("pairing timed out")
        null
    } catch (e: Exception) {
        null // socket died mid-handshake; the dial loop will retry
    }

    /** Verify the desktop's proof, then send ours. Mutual authentication. */
    private suspend fun authenticate(
        link: Link,
        resp: JSONObject,
        peerId: String,
        peerName: String,
        meName: String,
        meId: String,
        secret: ByteArray?,
        myNonce: String?,
        log: (String) -> Unit,
    ): Result? {
        if (secret == null || myNonce == null) return null // challenged for a key we lack

        val parts = (resp.stringOrNull("data") ?: "").split(":", limit = 2)
        val theirNonce = parts.getOrNull(0).orEmpty()
        val theirProof = parts.getOrNull(1).orEmpty()
        if (theirNonce.isEmpty() ||
            !sameProof(theirProof, proof(secret, myNonce, peerId, meId))
        ) {
            log("AUTH FAILED: $peerName could not prove it holds our pairing key — not connecting")
            return null
        }

        link.send(pairMsg("proof", meName, proof(secret, theirNonce, meId, peerId)))
        if (recvKind(link, "pair", log).stringOrNull("type") != "ok") {
            log("AUTH REJECTED by $peerName")
            return null
        }
        return Result(peerId, peerName)
    }

    /** First meeting: type the PIN, and store the key the desktop sends back. */
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
        log("PAIR REQUEST from $peerName — check the PIN on its screen")
        val pin = prompt.ask(peerName)
        if (pin == null) {
            log("pairing cancelled")
            return null
        }
        link.send(pairMsg("confirm", meName, pin.trim()))

        val final = recvKind(link, "pair", log)
        if (final.stringOrNull("type") != "ok") {
            log("PAIR DENIED by $peerName (wrong PIN)")
            return null
        }

        val key = hexToBytes(final.stringOrNull("data"))
        if (key == null || key.size != KEY_BYTES) {
            log("PAIR FAILED: $peerName sent no usable pairing key (is it running an older Yoink?)")
            return null
        }

        store.add(peerId, peerName, key)
        log("PAIRED with $peerName (saved; won't ask again)")
        return Result(peerId, peerName)
    }
}
