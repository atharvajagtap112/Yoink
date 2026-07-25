package com.yoink

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.AttributeSet
import android.view.View
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

    private val dotPaint = Paint().apply {
        color = Color.RED
        isAntiAlias = true
    }
    private val linePaint = Paint().apply {
        color = Color.parseColor("#40C4FF")
        strokeWidth = 6f
        isAntiAlias = true
    }
    private val posePaint = Paint().apply {
        textSize = 72f
        isFakeBoldText = true
        isAntiAlias = true
    }
    private val eventPaint = Paint().apply {
        color = Color.parseColor("#FF5252")
        textSize = 140f
        isFakeBoldText = true
        isAntiAlias = true
        textAlign = Paint.Align.CENTER
    }

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
                    linePaint,
                )
            }
        }
        for (p in landmarks) {
            canvas.drawCircle(p.x * width, p.y * height, 10f, dotPaint)
        }

        posePaint.color = when (pose) {
            Pose.OPEN -> Color.parseColor("#69F0AE")
            Pose.CLOSED -> Color.parseColor("#FFAB40")
            else -> Color.parseColor("#B0BEC5")
        }
        canvas.drawText(pose.name, 40f, 120f, posePaint)

        // Linger the last event ~1s so it's readable, not a one-frame flash.
        val event = lastEvent
        if (event != null && System.currentTimeMillis() - lastEventAt < 1000) {
            canvas.drawText(event.name, width / 2f, height / 2f, eventPaint)
        }
    }

    private companion object {
        val CONNECTIONS = arrayOf(
            0 to 1, 1 to 2, 2 to 3, 3 to 4, // thumb
            0 to 5, 5 to 6, 6 to 7, 7 to 8, // index
            5 to 9, 9 to 10, 10 to 11, 11 to 12, // middle
            9 to 13, 13 to 14, 14 to 15, 15 to 16, // ring
            13 to 17, 0 to 17, 17 to 18, 18 to 19, 19 to 20, // pinky
        )
    }
}
