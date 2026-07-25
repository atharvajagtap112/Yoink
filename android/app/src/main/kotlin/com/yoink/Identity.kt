package com.yoink

import android.content.Context
import android.os.Build
import java.security.SecureRandom

/**
 * Device identity, persisted so pairing survives a restart.
 *
 * Mirrors device_id()/--name in daemon/config.py. The id is what pairing keys
 * on, so it must be stable across launches; the name is cosmetic (it's the
 * `sender` field other devices display).
 */
data class Identity(val name: String, val deviceId: String) {

    companion object {
        private const val PREFS = "yoink.identity"
        private const val KEY_ID = "device_id"

        fun load(context: Context): Identity {
            val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            var id = prefs.getString(KEY_ID, null)
            if (id.isNullOrEmpty()) {
                // 12 hex chars, same shape as the Python side's uuid4().hex[:12].
                val bytes = ByteArray(6).also { SecureRandom().nextBytes(it) }
                id = bytes.joinToString("") { "%02x".format(it) }
                prefs.edit().putString(KEY_ID, id).apply()
            }
            return Identity(deviceName(), id)
        }

        /** e.g. "yoink-CPH2569" — recognisable in the desktop's logs. */
        private fun deviceName(): String {
            val model = Build.MODEL?.replace(Regex("[^A-Za-z0-9._-]"), "-").orEmpty()
            return if (model.isEmpty()) "yoink-phone" else "yoink-$model"
        }
    }
}
