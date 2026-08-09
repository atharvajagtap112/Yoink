package com.yoink

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import androidx.core.content.ContextCompat
import com.yoink.gesture.GestureEvent
import com.yoink.gesture.Point
import com.yoink.gesture.Pose

/**
 * Draws the hand skeleton, the current pose, and a SEND / RECEIVE flash over
 * the camera preview — the Android equivalent of the cv2 overlay in
 * daemon/gesture/camera.py, and there for the same reason: you cannot tune a
 * gesture you cannot see being classified.
 */
class OverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    private var landmarks: List<Point> = emptyList()
    private var pose: Pose = Pose.NO_HAND
    private var lastEvent: GestureEvent? = null
    private var lastEventAt = 0L

    private val density = resources.displayMetrics.density
    private fun dp(v: Float) = v * density

    private val accent = ContextCompat.getColor(context, R.color.accent)
    private val go = ContextCompat.getColor(context, R.color.go)
    private val warn = ContextCompat.getColor(context, R.color.warn)
    private val muted = ContextCompat.getColor(context, R.color.text_secondary)

    // Warm bones, not neon: a designed motion viz, not a CV debug overlay.
    private val bonePaint = Paint().apply {
        color = ContextCompat.getColor(context, R.color.bone)
        alpha = 205
        strokeWidth = dp(3.5f)
        strokeCap = Paint.Cap.ROUND
        isAntiAlias = true
    }
    private val jointPaint = Paint().apply {
        color = accent
        isAntiAlias = true
    }
    private val chipBgPaint = Paint().apply {
        color = ContextCompat.getColor(context, R.color.panel)
        alpha = 235
        isAntiAlias = true
    }
    private val chipDotPaint = Paint().apply { isAntiAlias = true }
    private val chipTextPaint = Paint().apply {
        color = ContextCompat.getColor(context, R.color.text_primary)
        textSize = dp(13f)
        isFakeBoldText = true
        isAntiAlias = true
    }
    private val flashBgPaint = Paint().apply { isAntiAlias = true }
    private val flashTextPaint = Paint().apply {
        textSize = dp(22f)
        isFakeBoldText = true
        letterSpacing = 0.08f
        isAntiAlias = true
        textAlign = Paint.Align.CENTER
    }

    // Fingertips read as slightly larger nodes than the knuckles.
    private val tips = setOf(4, 8, 12, 16, 20)

    fun update(points: List<Point>, newPose: Pose, event: GestureEvent?) {
        landmarks = points
        pose = newPose
        if (event != null) {
            lastEvent = event
            lastEventAt = System.currentTimeMillis()
        }
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        for ((a, b) in CONNECTIONS) {
            if (a < landmarks.size && b < landmarks.size) {
                canvas.drawLine(
                    landmarks[a].x * width, landmarks[a].y * height,
                    landmarks[b].x * width, landmarks[b].y * height,
                    bonePaint,
                )
            }
        }
        for ((i, p) in landmarks.withIndex()) {
            val r = if (i in tips) dp(6f) else dp(4.5f)
            canvas.drawCircle(p.x * width, p.y * height, r, jointPaint)
        }

        drawPoseChip(canvas)
        drawEventFlash(canvas)
    }

    /** A small centered pill naming the current pose — teaches the gesture. */
    private fun drawPoseChip(canvas: Canvas) {
        val (label, color) = when (pose) {
            Pose.OPEN -> "Open hand" to go
            Pose.CLOSED -> "Fist" to accent
            else -> "Show your hand" to muted
        }
        chipDotPaint.color = color

        val padH = dp(14f)
        val gap = dp(9f)
        val dotR = dp(4f)
        val textW = chipTextPaint.measureText(label)
        val chipW = padH + dotR * 2 + gap + textW + padH
        val chipH = dp(34f)
        val left = (width - chipW) / 2f
        val top = dp(22f)

        val rect = RectF(left, top, left + chipW, top + chipH)
        canvas.drawRoundRect(rect, chipH / 2f, chipH / 2f, chipBgPaint)

        val cy = top + chipH / 2f
        canvas.drawCircle(left + padH + dotR, cy, dotR, chipDotPaint)
        val baseline = cy - (chipTextPaint.descent() + chipTextPaint.ascent()) / 2f
        canvas.drawText(label, left + padH + dotR * 2 + gap, baseline, chipTextPaint)
    }

    /** On a fired gesture, a brief centered banner: "Sent" / "Caught". */
    private fun drawEventFlash(canvas: Canvas) {
        val event = lastEvent ?: return
        val age = System.currentTimeMillis() - lastEventAt
        if (age >= FLASH_MS) return

        val label = if (event == GestureEvent.SEND) "Sent" else "Caught"
        val color = if (event == GestureEvent.SEND) warn else go
        // Fade out over the last third of the linger.
        val alpha = (255 * (1f - (age.toFloat() / FLASH_MS)).coerceIn(0f, 1f) * 1.5f)
            .coerceIn(0f, 255f).toInt()

        val padH = dp(28f)
        val textW = flashTextPaint.measureText(label)
        val bannerW = textW + padH * 2
        val bannerH = dp(56f)
        val left = (width - bannerW) / 2f
        val top = height / 2f - bannerH / 2f

        flashBgPaint.color = color
        flashBgPaint.alpha = (alpha * 0.16f).toInt()
        canvas.drawRoundRect(
            RectF(left, top, left + bannerW, top + bannerH),
            bannerH / 2f, bannerH / 2f, flashBgPaint,
        )

        flashTextPaint.color = color
        flashTextPaint.alpha = alpha
        val cy = top + bannerH / 2f
        val baseline = cy - (flashTextPaint.descent() + flashTextPaint.ascent()) / 2f
        canvas.drawText(label, width / 2f, baseline, flashTextPaint)
    }

    private companion object {
        const val FLASH_MS = 1000L

        val CONNECTIONS = arrayOf(
            0 to 1, 1 to 2, 2 to 3, 3 to 4, // thumb
            0 to 5, 5 to 6, 6 to 7, 7 to 8, // index
            5 to 9, 9 to 10, 10 to 11, 11 to 12, // middle
            9 to 13, 13 to 14, 14 to 15, 15 to 16, // ring
            13 to 17, 0 to 17, 17 to 18, 18 to 19, 19 to 20, // pinky
        )
    }
}
