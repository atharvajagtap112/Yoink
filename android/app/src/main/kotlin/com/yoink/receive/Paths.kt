package com.yoink.receive

import android.content.Context
import java.io.File
import java.io.IOException

/**
 * Where caught files land, and how their names are made safe.
 *
 * Port of safe_filename()/unique_path()/save_dir() from daemon/config.py.
 *
 * The filename arrives from another machine, so it is untrusted: '../../evil'
 * or '/data/data/other.app/x' must not escape the save directory. We keep only
 * the last path component and scrub what a filesystem won't accept.
 */
object Paths {

    /**
     * Control chars plus the characters Windows rejects — kept identical to the
     * Python side so a name that survives one survives the other. Separators are
     * included as a second line of defence; the split below already removed them.
     */
    private val BAD = Regex("""[<>:"|?*/\\\x00-\x1f]""")

    fun safeFilename(name: String?, fallback: String = "yoink-payload"): String {
        if (name.isNullOrEmpty()) return fallback
        var n = name.replace('\\', '/').substringAfterLast('/') // drop any path part
        n = BAD.replace(n, "_").trim()
        n = n.trimEnd('.', ' ')
        if (n.isEmpty()) return fallback // was '', '.', '..' or all dots
        return if (n.length > 120) n.take(120) else n
    }

    /** A path that doesn't clobber an existing file: photo.png -> photo (1).png. */
    fun uniquePath(dir: File, filename: String): File {
        val first = File(dir, filename)
        if (!first.exists()) return first

        val dot = filename.lastIndexOf('.')
        val stem = if (dot > 0) filename.substring(0, dot) else filename
        val ext = if (dot > 0) filename.substring(dot) else ""
        for (i in 1 until 1000) {
            val candidate = File(dir, "$stem ($i)$ext")
            if (!candidate.exists()) return candidate
        }
        throw IOException("too many files named like $filename")
    }

    /**
     * The app-specific external directory, so no storage permission is needed
     * and our FileProvider can hand the file to other apps.
     */
    fun receivedDir(context: Context): File =
        File(context.getExternalFilesDir(null) ?: context.filesDir, "received")
            .apply { mkdirs() }
}
