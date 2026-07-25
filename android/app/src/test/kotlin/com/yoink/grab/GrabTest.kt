package com.yoink.grab

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Guards the clipboard url-vs-text decision in Grab.kt.
 *
 * The desktop decides the same thing with URL_RE in daemon/grab/router.py. If
 * the two disagree, a link sent from the phone arrives as plain text and the
 * laptop copies it instead of opening it — a silent, annoying failure.
 * Run: gradlew :app:testDebugUnitTest
 */
class GrabTest {

    @Test
    fun httpLinksAreSentAsUrl() {
        listOf(
            "https://example.com",
            "http://example.com",
            "https://youtube.com/watch?v=abc&t=142s",
            "HTTPS://EXAMPLE.COM", // the desktop regex is case-insensitive too
            "https://example.com/a/b?c=d#e",
        ).forEach { assertEquals(it, "url", Grab.clipboardType(it)) }
    }

    @Test
    fun surroundingWhitespaceDoesNotChangeTheVerdict() {
        assertEquals("url", Grab.clipboardType("  https://example.com \n"))
    }

    @Test
    fun anythingElseIsSentAsText() {
        listOf(
            "hello world",
            "ftp://example.com", // not http(s): the desktop wouldn't open it either
            "file:///C:/x.pdf",
            "example.com", // no scheme
            "see https://example.com for details", // a sentence, not a bare link
            "https://example.com and more", // trailing content -> \S+ fails
            "chrome://settings",
        ).forEach { assertEquals(it, "text", Grab.clipboardType(it)) }
    }
}
