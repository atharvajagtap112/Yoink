package com.yoink.receive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * Guards the untrusted-filename handling in Paths.kt (port of the Dart
 * paths_test.dart). Traversal and collision are the two ways this hurts:
 * escaping the save directory, or silently eating a file you already caught.
 * Run: gradlew :app:testDebugUnitTest
 */
class PathsTest {

    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun keepsOrdinaryNames() {
        assertEquals("report.pdf", Paths.safeFilename("report.pdf"))
        assertEquals("holiday photo.png", Paths.safeFilename("holiday photo.png"))
    }

    @Test
    fun stripsAnyPathComponent() {
        assertEquals("passwd", Paths.safeFilename("../../etc/passwd"))
        assertEquals("evil.dll", Paths.safeFilename("""C:\windows\system32\evil.dll"""))
        assertEquals("db", Paths.safeFilename("/data/data/other.app/db"))
        assertEquals("d.txt", Paths.safeFilename("""a/b\c/d.txt"""))
    }

    @Test
    fun fallsBackWhenNothingUsableSurvives() {
        assertEquals("yoink-payload", Paths.safeFilename(null))
        assertEquals("yoink-payload", Paths.safeFilename(""))
        assertEquals("yoink-payload", Paths.safeFilename(".."))
        assertEquals("yoink-payload", Paths.safeFilename("."))
        assertEquals("yoink-payload", Paths.safeFilename("..."))
        assertEquals("yoink-payload", Paths.safeFilename("/"))
        assertEquals("x", Paths.safeFilename("x", "yoink-image"))
        assertEquals("yoink-image", Paths.safeFilename("..", "yoink-image"))
    }

    @Test
    fun scrubsCharactersAFilesystemRejects() {
        assertEquals("a_b_c_d_e_f_g_h.txt", Paths.safeFilename("""a<b>c:d"e|f?g*h.txt"""))
        assertEquals("bell_.txt", Paths.safeFilename("bell\u0007.txt")) // control chars too
    }

    @Test
    fun trimsTrailingDotsAndSpaces() {
        assertEquals("name", Paths.safeFilename("name.   "))
        assertEquals("name", Paths.safeFilename("name..."))
    }

    @Test
    fun capsTheLength() {
        assertEquals(120, Paths.safeFilename("a".repeat(500) + ".png").length)
    }

    @Test
    fun theResultCanNeverEscapeItsDirectory() {
        val nasty = listOf(
            "../../../etc/passwd",
            """..\..\windows\evil.exe""",
            "....//....//x",
            "/absolute/path",
            "..",
        )
        for (n in nasty) {
            val safe = Paths.safeFilename(n)
            assertTrue(n, !safe.contains('/'))
            assertTrue(n, !safe.contains('\\'))
            assertNotEquals(n, "..", safe)
        }
    }

    @Test
    fun uniquePathUsesThePlainNameWhenFree() {
        val dir = tmp.newFolder()
        assertEquals(java.io.File(dir, "a.png"), Paths.uniquePath(dir, "a.png"))
    }

    @Test
    fun uniquePathNeverOverwritesAnExistingCatch() {
        val dir = tmp.newFolder()
        java.io.File(dir, "photo.png").writeText("first")
        val second = Paths.uniquePath(dir, "photo.png")
        assertEquals(java.io.File(dir, "photo (1).png"), second)

        second.writeText("second")
        assertEquals(java.io.File(dir, "photo (2).png"), Paths.uniquePath(dir, "photo.png"))
        // The original survived, which is the whole point.
        assertEquals("first", java.io.File(dir, "photo.png").readText())
    }

    @Test
    fun uniquePathHandlesNamesWithNoExtension() {
        val dir = tmp.newFolder()
        java.io.File(dir, "README").writeText("x")
        assertEquals(java.io.File(dir, "README (1)"), Paths.uniquePath(dir, "README"))
    }

    @Test
    fun uniquePathTreatsALeadingDotAsPartOfTheName() {
        val dir = tmp.newFolder()
        java.io.File(dir, ".gitignore").writeText("x")
        assertEquals(java.io.File(dir, ".gitignore (1)"), Paths.uniquePath(dir, ".gitignore"))
    }
}
