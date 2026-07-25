package com.yoink.gesture

import kotlin.math.hypot

/**
 * Single-frame gesture classifier: 21 hand landmarks -> OPEN / CLOSED / UNKNOWN.
 *
 * Port of daemon/gesture/classifier.py — the desktop is the reference, and the
 * two must agree or the same hand reads differently on each device.
 *
 * Pure geometry, no trained model. A finger counts as *extended* when its tip
 * sits clearly farther from the wrist than its PIP joint, and *curled* when
 * clearly closer. Using distances (not up/down) keeps it independent of hand
 * orientation.
 */

enum class Pose { OPEN, CLOSED, UNKNOWN, NO_HAND }

/** A normalized landmark: x and y both in 0..1. */
data class Point(val x: Float, val y: Float)

private const val WRIST = 0

/** (pip, tip) landmark indices for index, middle, ring, pinky. */
private val FINGERS = arrayOf(6 to 8, 10 to 12, 14 to 16, 18 to 20)

/** (mcp, tip) for the thumb. */
private val THUMB = 2 to 4

// Two thresholds with a dead zone in between, so mid-motion frames read as
// UNKNOWN instead of flickering OPEN<->CLOSED. Tune on the live overlay.
const val EXTENDED_RATIO = 1.05f
const val CURLED_RATIO = 0.95f

private fun dist(a: Point, b: Point) = hypot(a.x - b.x, a.y - b.y)

private fun ratio(lm: List<Point>, base: Int, tip: Int): Float {
    val dBase = dist(lm[WRIST], lm[base])
    if (dBase == 0f) return 1f
    return dist(lm[WRIST], lm[tip]) / dBase
}

fun classify(lm: List<Point>): Pose {
    if (lm.size < 21) return Pose.UNKNOWN

    var extended = 0
    var curled = 0
    for ((pip, tip) in FINGERS) {
        val r = ratio(lm, pip, tip)
        if (r >= EXTENDED_RATIO) extended++ else if (r <= CURLED_RATIO) curled++
    }

    val thumbExtended = ratio(lm, THUMB.first, THUMB.second) >= EXTENDED_RATIO

    // Forgiving on the thumb (it folds sideways, not toward the wrist): OPEN
    // needs all four fingers out; CLOSED needs all four curled.
    if (extended == 4 && thumbExtended) return Pose.OPEN
    if (curled == 4) return Pose.CLOSED
    return Pose.UNKNOWN
}
