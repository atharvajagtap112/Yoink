package com.yoink.net

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Guards the authentication proof against daemon/net/pairing.py.
 *
 * The desktop and the phone must compute the same MAC from the same inputs or
 * they simply stop connecting — and the failure is silent, because a bad proof
 * looks exactly like a peer that isn't there. The pinned vector below is the
 * cheapest possible tripwire for that.
 *
 * Store is not covered here: it needs an Android Context, and these are plain
 * JVM tests with no emulator. Its logic is a thin SharedPreferences wrapper; the
 * behaviour that matters (a pre-key record must read as unpaired) is asserted on
 * the Python side in net/test_mesh.py and verified on-device by re-pairing.
 *
 * Run: gradlew :app:testDebugUnitTest
 */
class PairingTest {

    private val key = Pairing.hexToBytes(
        "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
    )!!
    private val nonce = "a1b2c3d4e5f60718293a4b5c6d7e8f90"

    /**
     * Known-answer vector, identical to the one in daemon/net/test_mesh.py.
     * If either side changes the message format, exactly one of these two tests
     * fails and the mismatch is obvious instead of mysterious.
     */
    @Test
    fun proofMatchesThePythonDaemon() {
        assertEquals(
            "afdfe4991915e62fc4e6fa3c80cbabe0dde97d77b67e73476cfb34ba166ff2c5",
            Pairing.proof(key, nonce, "aaaaaaaaaaaa", "bbbbbbbbbbbb"),
        )
    }

    /**
     * A proof names who is proving to whom. Without that, an attacker could
     * bounce our own challenge straight back at us and pass without holding the
     * key at all.
     */
    @Test
    fun proofIsDirectionBound() {
        assertNotEquals(
            Pairing.proof(key, nonce, "aaaaaaaaaaaa", "bbbbbbbbbbbb"),
            Pairing.proof(key, nonce, "bbbbbbbbbbbb", "aaaaaaaaaaaa"),
        )
    }

    @Test
    fun proofChangesWithTheKeyAndTheNonce() {
        val base = Pairing.proof(key, nonce, "aaaaaaaaaaaa", "bbbbbbbbbbbb")
        assertNotEquals(base, Pairing.proof(ByteArray(32), nonce, "aaaaaaaaaaaa", "bbbbbbbbbbbb"))
        assertNotEquals(
            base,
            Pairing.proof(key, "00000000000000000000000000000000", "aaaaaaaaaaaa", "bbbbbbbbbbbb"),
        )
    }

    @Test
    fun hexRoundTrips() {
        val bytes = byteArrayOf(0, 1, 15, 16, 127, -1, -128)
        assertArrayEquals(bytes, Pairing.hexToBytes(Pairing.toHex(bytes)))
        assertEquals("00010f107fff80", Pairing.toHex(bytes))
    }

    /**
     * The pairing key arrives over the wire, so malformed hex must produce null
     * rather than an exception or a silently truncated key.
     */
    @Test
    fun hexRejectsGarbage() {
        listOf(null, "", "abc", "zz", "12 34", "0x1234").forEach {
            assertNull("should reject: $it", Pairing.hexToBytes(it))
        }
    }
}
