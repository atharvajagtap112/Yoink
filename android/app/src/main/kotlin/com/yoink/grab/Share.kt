package com.yoink.grab

import android.content.ContentResolver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Base64
import android.webkit.MimeTypeMap
import com.yoink.net.Protocol
import com.yoink.receive.Paths
import org.json.JSONObject

/**
 * Turn an Android "Share → Yoink" intent into wire envelopes (milestone K6).
 *
 * This is the one path to a *real* file off the phone. A gesture grab can only
 * ever get clipboard text or a screenshot (DESIGN.md section 3a); the share
 * sheet hands us the actual bytes, filename and MIME the source app chose. The
 * cost is that it's a tap, not a gesture — a complement to the SEND grab, not a
 * replacement.
 *
 * The desktop's 5a dispatcher already opens whatever we send, so a shared PDF
 * arrives as a `file` and opens in the laptop's viewer with no desktop change.
 */
object Share {

    /**
     * Build one envelope per shared item. Empty if the intent carried nothing
     * usable or everything was over the size limit.
     */
    fun fromSendIntent(
        context: Context,
        intent: Intent,
        sender: String,
        log: (String) -> Unit,
    ): List<JSONObject> = when (intent.action) {
        Intent.ACTION_SEND -> {
            val uri = intent.getParcelableExtraCompat<Uri>(Intent.EXTRA_STREAM)
            when {
                uri != null -> listOfNotNull(fileEnvelope(context, uri, sender, log))
                else -> {
                    val text = intent.getStringExtra(Intent.EXTRA_TEXT)?.trim()
                    if (text.isNullOrEmpty()) emptyList()
                    else listOf(textEnvelope(text, sender, log))
                }
            }
        }

        Intent.ACTION_SEND_MULTIPLE -> {
            intent.getParcelableArrayListExtraCompat<Uri>(Intent.EXTRA_STREAM)
                .mapNotNull { fileEnvelope(context, it, sender, log) }
        }

        else -> emptyList()
    }

    private fun textEnvelope(text: String, sender: String, log: (String) -> Unit): JSONObject {
        val type = Grab.clipboardType(text)
        log("share: text as $type (${text.length} chars)")
        return Protocol.envelope("payload", type = type, data = text, sender = sender)
    }

    private fun fileEnvelope(
        context: Context,
        uri: Uri,
        sender: String,
        log: (String) -> Unit,
    ): JSONObject? {
        val resolver = context.contentResolver
        val bytes = try {
            resolver.openInputStream(uri)?.use { it.readBytes() }
        } catch (e: Exception) {
            log("share: can't read $uri: $e")
            null
        } ?: return null

        if (bytes.size > Grab.MAX_GRAB_BYTES) {
            log("share: ${bytes.size / 1_000_000} MB is over the ${Grab.MAX_GRAB_BYTES / 1_000_000} MB limit — skipped")
            return null
        }

        val name = Paths.safeFilename(displayName(resolver, uri), "yoink-file")
        val mime = resolver.getType(uri) ?: mimeFromName(name)
        // Match the desktop's split: an image previews, anything else opens by
        // association. A shared photo is an image; a PDF or doc is a file.
        val type = if (mime.startsWith("image/")) "image" else "file"
        log("share: $type $name (${bytes.size} bytes, $mime)")
        return Protocol.envelope(
            "payload",
            type = type,
            filename = name,
            mime = mime,
            data = Base64.encodeToString(bytes, Base64.NO_WRAP),
            sender = sender,
        )
    }

    /** The source app's chosen filename, via the OpenableColumns contract. */
    private fun displayName(resolver: ContentResolver, uri: Uri): String? {
        if (uri.scheme == ContentResolver.SCHEME_FILE) return uri.lastPathSegment
        return try {
            resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)
                ?.use { c -> if (c.moveToFirst()) c.getString(0) else null }
        } catch (e: Exception) {
            uri.lastPathSegment
        }
    }

    private fun mimeFromName(name: String): String {
        val ext = name.substringAfterLast('.', "").lowercase()
        return MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext)
            ?: "application/octet-stream"
    }
}

// getParcelableExtra(String) is deprecated from API 33; these keep one call site
// while staying correct on both sides of that line. minSdk is 26.
private inline fun <reified T : android.os.Parcelable> Intent.getParcelableExtraCompat(name: String): T? =
    if (android.os.Build.VERSION.SDK_INT >= 33) {
        getParcelableExtra(name, T::class.java)
    } else {
        @Suppress("DEPRECATION") getParcelableExtra(name) as? T
    }

private inline fun <reified T : android.os.Parcelable> Intent.getParcelableArrayListExtraCompat(name: String): List<T> =
    if (android.os.Build.VERSION.SDK_INT >= 33) {
        getParcelableArrayListExtra(name, T::class.java) ?: emptyList()
    } else {
        @Suppress("DEPRECATION") (getParcelableArrayListExtra<T>(name) ?: emptyList())
    }
