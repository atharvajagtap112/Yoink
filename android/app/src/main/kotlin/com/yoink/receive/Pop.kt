package com.yoink.receive

import android.content.Context
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

/**
 * The always-on-top "caught" pop (DESIGN.md section 3, receive path).
 *
 * Android counterpart of daemon/receive/toast.py: shows the caught item's name,
 * a thumbnail when it's an image, and opens the item when tapped.
 *
 * A real overlay window rather than a Toast, because the pop must be visible
 * *and tappable* while you're in another app — a Toast is neither interactive
 * nor reliable from the background. This needs SYSTEM_ALERT_WINDOW, which we
 * want anyway: it's the same permission that lets the RECEIVE gesture launch
 * anything at all from the background (section 3a).
 */
class Pop(private val context: Context) {

    private val windows by lazy { context.getSystemService(WindowManager::class.java) }
    private val main = Handler(Looper.getMainLooper())
    private var current: View? = null
    private var dismiss: Runnable? = null

    fun show(caught: Dispatch.Caught, onTap: () -> Unit) {
        if (!Settings.canDrawOverlays(context)) {
            // Degrade rather than crash: without the permission we can't draw
            // over other apps, and the gesture-open is broken too. Say so.
            Toast.makeText(
                context,
                "caught: ${caught.label} — enable Display over other apps to open it",
                Toast.LENGTH_LONG,
            ).show()
            return
        }
        main.post { showOverlay(caught, onTap) }
    }

    private fun showOverlay(caught: Dispatch.Caught, onTap: () -> Unit) {
        hide()

        val view = buildView(caught) {
            hide()
            onTap()
        }
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            // NOT_FOCUSABLE so we never steal the keyboard from the app
            // underneath, but deliberately NOT touchable-through: the whole
            // point is that you can tap this.
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            android.graphics.PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP
            y = 80
        }

        try {
            windows.addView(view, params)
            current = view
        } catch (e: Exception) {
            return // permission revoked between the check and here
        }

        dismiss = Runnable { hide() }.also { main.postDelayed(it, DURATION_MS) }
    }

    private fun buildView(caught: Dispatch.Caught, onTap: () -> Unit): View {
        val density = context.resources.displayMetrics.density
        fun dp(v: Int) = (v * density).toInt()

        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(14), dp(12), dp(14), dp(12))
            background = GradientDrawable().apply {
                setColor(Color.parseColor("#EE222222"))
                cornerRadius = dp(12).toFloat()
            }
            setOnClickListener { onTap() }
        }

        if (caught.isImage) {
            // Best effort: a pop without its picture still beats no pop at all.
            BitmapFactory.decodeFile(caught.file!!.absolutePath)?.let { bitmap ->
                row.addView(
                    ImageView(context).apply {
                        setImageBitmap(bitmap)
                        scaleType = ImageView.ScaleType.CENTER_CROP
                    },
                    LinearLayout.LayoutParams(dp(56), dp(56)).apply {
                        rightMargin = dp(12)
                    },
                )
            }
        }

        val column = LinearLayout(context).apply { orientation = LinearLayout.VERTICAL }
        column.addView(
            TextView(context).apply {
                text = "caught: ${caught.label}"
                setTextColor(Color.WHITE)
                textSize = 15f
                maxLines = 2
            }
        )
        column.addView(
            TextView(context).apply {
                text = "from ${caught.sender} — open your hand, or tap"
                setTextColor(Color.parseColor("#B0BEC5"))
                textSize = 12f
                maxLines = 1
            }
        )
        row.addView(column, LinearLayout.LayoutParams(0, -2, 1f))

        // Margin so the card doesn't run edge to edge.
        return LinearLayout(context).apply {
            setPadding(dp(12), 0, dp(12), 0)
            addView(row, LinearLayout.LayoutParams(-1, -2))
        }
    }

    fun hide() {
        dismiss?.let { main.removeCallbacks(it) }
        dismiss = null
        current?.let {
            try {
                windows.removeView(it)
            } catch (e: IllegalArgumentException) {
                // already gone
            }
        }
        current = null
    }

    private companion object {
        const val DURATION_MS = 6_000L
    }
}
