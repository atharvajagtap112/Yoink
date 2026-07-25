package com.yoink.grab

import android.content.ClipboardManager
import android.content.Context
import android.util.Base64
import com.yoink.ScreenCaptureService
import com.yoink.net.Protocol
import org.json.JSONObject

/**
 * Decide WHAT to grab on a SEND gesture (DESIGN.md section 3, send path).
 *
 * The phone's counterpart to daemon/grab/router.py, and deliberately much
 * smaller. Android gives an app no cross-app foreground file access at all
 * (section 3a), so the ladder is only two rungs:
 *
 *     clipboard has text -> http(s) url? send `url`, else send `text`
 *     nothing usable     -> screenshot the screen -> send `image`
 *
 * The screenshot is the universal fallback, exactly as on the desktop. Two
 * Android limits shape this: the clipboard is readable only by the focused app
 * (so a backgrounded grab is screenshot-only), and the screenshot needs a live
 * MediaProjection the user consented to (so with consent declined, a
 * backgrounded grab has nothing to send).
 */
object Grab {

    /**
     * Biggest thing a grab will send, mirroring MAX_GRAB_BYTES in
     * daemon/config.py. Must stay under the desktop's MAX_WS_MESSAGE (48 MiB).
     */
    const val MAX_GRAB_BYTES = 25 * 1_000_000

    /** Same shape as URL_RE in daemon/grab/router.py. */
    private val URL = Regex("""^https?://\S+$""", RegexOption.IGNORE_CASE)

    /** "url" or "text" for a non-empty clipboard string. Pure, so it's testable. */
    fun clipboardType(text: String): String =
        if (URL.matches(text.trim())) "url" else "text"

    /**
     * Grab whatever the user is pointing at. Returns an envelope, or null if
     * there was nothing to send. [capture] blocks on a frame, so call this off
     * the main thread.
     */
    fun grab(context: Context, sender: String, log: (String) -> Unit): JSONObject? {
        val clip = clipboardText(context)
        if (!clip.isNullOrEmpty()) {
            val type = clipboardType(clip)
            log("grab: strategy=clipboard $type (${clip.length} chars)")
            return Protocol.envelope("payload", type = type, data = clip, sender = sender)
        }

        val capture = ScreenCaptureService.instance
        if (capture == null || !capture.isReady()) {
            log("grab: clipboard empty and screen capture is off — nothing to grab")
            return null
        }
        log("grab: clipboard empty -> screenshot (fallback)")
        val png = capture.capture()
        if (png == null) {
            log("grab: screenshot failed — nothing to grab")
            return null
        }
        if (png.size > MAX_GRAB_BYTES) {
            log("grab: screenshot is ${png.size / 1_000_000} MB, over the limit — not sending")
            return null
        }
        log("grab: strategy=screenshot (${png.size} bytes)")
        return Protocol.envelope(
            "payload",
            type = "image",
            filename = "screen.png",
            mime = "image/png",
            data = Base64.encodeToString(png, Base64.NO_WRAP),
            sender = sender,
        )
    }

    /** Clipboard text, or null. Only returns anything while Yoink is focused. */
    private fun clipboardText(context: Context): String? {
        val clipboard = context.getSystemService(ClipboardManager::class.java)
        val clip = clipboard?.primaryClip ?: return null
        if (clip.itemCount == 0) return null
        return clip.getItemAt(0).coerceToText(context).toString().trim()
    }
}
