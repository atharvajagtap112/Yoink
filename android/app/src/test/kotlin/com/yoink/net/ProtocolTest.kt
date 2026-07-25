package com.yoink.net

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Guards wire compatibility with daemon/net/protocol.py.
 *
 * The interop case below is a literal string of the shape the Python side
 * emits — if the desktop's envelope ever drifts, this fails here rather than
 * silently on the phone.
 *
 * Runs on the JVM against the real org.json (Android's is the same API), so the
 * `put(key, null)` deletion trap is genuinely exercised rather than assumed.
 * Run: gradlew :app:testDebugUnitTest
 */
class ProtocolTest {

    @Test
    fun roundTrip() {
        val e = Protocol.envelope("payload", type = "text", data = "hello world", sender = "laptop-a")
        val decoded = Protocol.decode(Protocol.encode(e))!!
        assertEquals("payload", decoded.stringOrNull("kind"))
        assertEquals("text", decoded.stringOrNull("type"))
        assertEquals("hello world", decoded.stringOrNull("data"))
        assertEquals("laptop-a", decoded.stringOrNull("sender"))
    }

    @Test
    fun rejectsNonEnvelopes() {
        assertNull(Protocol.decode("not json"))
        assertNull(Protocol.decode("[1,2,3]")) // valid JSON, not an object
        assertNull(Protocol.decode("""{"foo": 1}""")) // object but no kind
    }

    /**
     * The org.json trap: `put(key, null)` removes the key entirely. If that
     * regressed, `filename`/`mime`/`type` would vanish from the envelope and
     * the desktop would see a malformed payload.
     */
    @Test
    fun everySpecFieldIsPresentAndNullWhenUnset() {
        val e = Protocol.envelope("heartbeat", sender = "phone")
        val keys = e.keys().asSequence().toSet()
        assertEquals(setOf("v", "kind", "type", "filename", "mime", "data", "sender", "ts"), keys)
        assertEquals(1, e.getInt("v"))
        assertTrue(e.isNull("type"))
        assertTrue(e.isNull("filename"))
        assertTrue(e.isNull("mime"))
        assertTrue(e.isNull("data"))
        // And an explicit JSON null must read back as a Kotlin null, not "null".
        assertNull(e.stringOrNull("type"))
    }

    @Test
    fun decodesWhatThePythonDaemonActuallySends() {
        // Exactly the shape of protocol.envelope() in daemon/net/protocol.py,
        // taken from DESIGN.md section 6.
        val fromPython = """
            {"v": 1, "kind": "payload", "type": "url", "filename": null,
             "mime": null, "data": "https://youtube.com/watch?v=abc&t=142s",
             "sender": "atharva-laptop", "ts": 1720900000}
        """.trimIndent()
        val env = Protocol.decode(fromPython)!!
        assertEquals("payload", env.stringOrNull("kind"))
        assertEquals("url", env.stringOrNull("type"))
        assertEquals("https://youtube.com/watch?v=abc&t=142s", env.stringOrNull("data"))
        assertNull(env.stringOrNull("filename"))
        assertTrue(Protocol.describe(env).startsWith("url https://youtube.com"))
    }

    @Test
    fun describesBlobsByFilenameNotBase64() {
        val e = Protocol.envelope(
            "payload",
            type = "image",
            data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
            filename = "shot.png",
            mime = "image/png",
            sender = "laptop",
        )
        assertEquals("image shot.png", Protocol.describe(e))
    }

    @Test
    fun pairMessagesCarryTheStepInType() {
        // The handshake rides on `type`; a regression here breaks pairing only,
        // which is easy to misread as a networking fault.
        val e = Protocol.envelope("pair", type = "confirm", data = "4821", sender = "phone")
        assertEquals("pair", e.stringOrNull("kind"))
        assertEquals("confirm", e.stringOrNull("type"))
        assertEquals("4821", e.stringOrNull("data"))
    }

    @Test
    fun decodeSurvivesGarbageWithoutThrowing() {
        // Anything can arrive on a socket; decode must return null, never throw.
        listOf("", "   ", "{", "null", "42", """{"kind":}""").forEach {
            assertNull("should reject: $it", Protocol.decode(it))
        }
    }
}
