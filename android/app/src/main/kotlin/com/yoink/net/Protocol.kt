package com.yoink.net

import org.json.JSONObject

/**
 * JSON wire envelope (CLAUDE.md section 6). Encode/decode only — no I/O.
 *
 * Port of daemon/net/protocol.py. The desktop daemon is the reference
 * implementation; these field names and types must match it exactly or the two
 * sides stop understanding each other.
 *
 * org.json trap: `put(key, null)` *removes* the key rather than storing a null,
 * which would silently drop `filename`/`mime`/`type` from the envelope. Use
 * [JSONObject.NULL] to write an explicit JSON null, and [stringOrNull] to read
 * one back — plain `optString` hands you the literal text "null".
 */
object Protocol {

    const val VERSION = 1

    /** Build a full envelope. Unused fields stay null, per the spec. */
    fun envelope(
        kind: String, // hello | pair | heartbeat | payload
        type: String? = null, // url | text | image | file  (payload only)
        data: String? = null, // raw string for url/text; base64 for image/file
        sender: String = "",
        filename: String? = null,
        mime: String? = null,
        ts: Long = System.currentTimeMillis() / 1000,
    ): JSONObject = JSONObject().apply {
        put("v", VERSION)
        put("kind", kind)
        put("type", type ?: JSONObject.NULL)
        put("filename", filename ?: JSONObject.NULL)
        put("mime", mime ?: JSONObject.NULL)
        put("data", data ?: JSONObject.NULL)
        put("sender", sender)
        put("ts", ts)
    }

    fun encode(env: JSONObject): String = env.toString()

    /**
     * Parse a wire message. Returns null if it isn't a valid envelope (bad
     * JSON / not an object / missing kind).
     */
    fun decode(raw: String): JSONObject? = try {
        JSONObject(raw).takeIf { it.has("kind") }
    } catch (e: Exception) {
        null // not JSON, or a JSON array rather than an object
    }

    /**
     * Short human-readable description of a payload, for logs and the pop.
     * image/file carry base64 in `data`, so show the filename instead of 40
     * characters of base64.
     */
    fun describe(env: JSONObject): String {
        val type = env.stringOrNull("type") ?: "?"
        val filename = env.stringOrNull("filename")
        if (!filename.isNullOrEmpty()) return "$type $filename"
        val data = env.stringOrNull("data").orEmpty()
        val head = if (data.length > 40) data.take(40) + "..." else data
        return "$type $head"
    }
}

/** null for a missing key *and* for an explicit JSON null. */
fun JSONObject.stringOrNull(key: String): String? =
    if (isNull(key)) null else optString(key)
