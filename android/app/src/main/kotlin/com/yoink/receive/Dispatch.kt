package com.yoink.receive

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.util.Base64
import android.webkit.MimeTypeMap
import androidx.core.content.FileProvider
import com.yoink.net.stringOrNull
import java.io.File
import org.json.JSONObject

/**
 * Type -> save + open by association (DESIGN.md section 3, receive path).
 *
 * Port of daemon/receive/dispatch.py:
 *     url   -> open in the default browser
 *     image -> save to disk -> preview pop (tap to open full size)
 *     file  -> save to disk -> open with the OS default app
 *     text  -> write to the clipboard -> pop
 *
 * The sender already decided the type, so this side never asks "what app opens
 * a .docx". It hands the file to Android and lets the MIME association answer.
 *
 * Split into two phases, unlike the desktop's single dispatch():
 *   [prepare] runs on arrival — decode and save, so the pop can show a real
 *             thumbnail — but opens nothing.
 *   [open]    runs on the RECEIVE gesture, or when you tap the pop.
 * Keeping the open separate means an arriving payload never launches an app
 * behind your back; it waits for the gesture, exactly like the desktop.
 *
 * Opening from the background (the point of the whole Kotlin rewrite) needs
 * SYSTEM_ALERT_WINDOW — see DESIGN.md section 3a. Without it these intents are
 * silently swallowed.
 */
object Dispatch {

    /** One received payload, decoded and saved, ready to be opened. */
    data class Caught(
        val type: String, // text | url | image | file
        val label: String, // what the pop shows
        val sender: String,
        val file: File? = null, // set for image/file
        val text: String? = null, // set for text (and a url we refused to launch)
        val url: String? = null, // set for a launchable http(s) url
        val mime: String? = null, // from the envelope; else guessed from extension
    ) {
        val isImage: Boolean get() = type == "image" && file != null
    }

    /** Decode and save one arriving payload. Returns null if it isn't usable. */
    fun prepare(context: Context, env: JSONObject, log: (String) -> Unit): Caught? {
        val kind = env.stringOrNull("type")
        val sender = env.stringOrNull("sender") ?: "?"
        val data = env.stringOrNull("data")
        if (data == null) {
            log("dispatch: payload from $sender has no data — ignored")
            return null
        }

        return when (kind) {
            "text" -> Caught("text", short(data), sender, text = data)

            "url" -> {
                // Only ever hand http(s) to the OS. An exotic scheme would launch
                // whatever handler happens to be registered for it — not something
                // to do with a string that arrived over the network.
                val lower = data.lowercase()
                if (lower.startsWith("http://") || lower.startsWith("https://")) {
                    Caught("url", short(data), sender, url = data)
                } else {
                    log("CAUGHT url <- $sender: ${short(data)} isn't openable, copied instead")
                    Caught("text", short(data), sender, text = data)
                }
            }

            "image", "file" -> {
                val raw = try {
                    Base64.decode(data, Base64.DEFAULT)
                } catch (e: IllegalArgumentException) {
                    log("dispatch: bad base64 in $kind from $sender: $e")
                    return null
                }
                val name = Paths.safeFilename(env.stringOrNull("filename"), "yoink-$kind")
                val file = Paths.uniquePath(Paths.receivedDir(context), name)
                file.writeBytes(raw)
                log("CAUGHT $kind <- $sender: saved ${file.name} (${raw.size} bytes)")
                Caught(kind, file.name, sender, file = file, mime = env.stringOrNull("mime"))
            }

            else -> {
                log("dispatch: unknown payload type $kind from $sender — ignored")
                null
            }
        }
    }

    /**
     * Act on a prepared payload: clipboard, browser, or the OS file
     * association. Returns a short line describing what happened.
     */
    fun open(context: Context, caught: Caught, log: (String) -> Unit): String = when (caught.type) {
        "text" -> {
            val clipboard = context.getSystemService(ClipboardManager::class.java)
            clipboard.setPrimaryClip(ClipData.newPlainText("Yoink", caught.text))
            log("CAUGHT text <- ${caught.sender}: copied to clipboard")
            "copied to clipboard"
        }

        "url" -> launch(
            context,
            Intent(Intent.ACTION_VIEW, Uri.parse(caught.url)),
            "opened in browser",
            log,
        )

        "image", "file" -> {
            // A raw file:// URI throws FileUriExposedException on modern
            // Android; another app can only read ours through a content:// URI
            // with read permission granted for this one intent.
            val uri = FileProvider.getUriForFile(context, AUTHORITY, caught.file!!)
            val intent = Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, mimeOf(caught))
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            launch(context, intent, "opened ${caught.label}", log)
        }

        else -> "nothing to open"
    }

    private fun launch(
        context: Context,
        intent: Intent,
        okMessage: String,
        log: (String) -> Unit,
    ): String = try {
        // NEW_TASK is mandatory when starting an activity from a service.
        context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        log("open -> $okMessage")
        okMessage
    } catch (e: android.content.ActivityNotFoundException) {
        log("open -> no app installed for this type")
        "saved — no app installed for this type"
    } catch (e: SecurityException) {
        // The background-activity-launch path: Android refused because
        // "Display over other apps" is off. Actionable, so say so plainly.
        log("open -> refused: $e")
        "Android blocked it — enable Display over other apps"
    }

    /** The envelope's mime wins; otherwise guess from the extension. */
    private fun mimeOf(caught: Caught): String {
        caught.mime?.takeIf { it.isNotEmpty() }?.let { return it }
        val ext = caught.file?.extension?.lowercase().orEmpty()
        return MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext)
            ?: "application/octet-stream"
    }

    private fun short(s: String) = if (s.length > 40) s.take(40) + "..." else s

    private const val AUTHORITY = "com.yoink.fileprovider"
}
