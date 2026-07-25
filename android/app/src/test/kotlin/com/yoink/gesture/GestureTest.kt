package com.yoink.gesture

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Port of daemon/gesture/test_gesture.py — same cases, same expectations.
 *
 * Runs as a plain JVM test (no emulator): the state machine's clock is injected,
 * so cooldown is tested by moving a fake clock rather than sleeping.
 * Run: gradlew :app:testDebugUnitTest
 */
class GestureTest {

    /** 21 fake landmarks. curl=false -> open (tips far from the wrist at 0,1). */
    private fun hand(curl: Boolean): List<Point> {
        val lm = MutableList(21) { Point(0.5f, 0.5f) }
        lm[0] = Point(0.5f, 1.0f) // wrist at the bottom
        for ((base, tip) in listOf(2 to 4, 6 to 8, 10 to 12, 14 to 16, 18 to 20)) {
            lm[base] = Point(0.5f, 0.6f)
            lm[tip] = if (curl) Point(0.5f, 0.7f) else Point(0.5f, 0.3f)
        }
        return lm
    }

    @Test
    fun classifier() {
        assertEquals(Pose.OPEN, classify(hand(curl = false)))
        assertEquals(Pose.CLOSED, classify(hand(curl = true)))
        assertEquals(Pose.UNKNOWN, classify(emptyList()))
    }

    @Test
    fun debounceAndEdge() {
        val sm = GestureStateMachine(debounce = 3, cooldownMs = 0) { 0L }
        // First stable pose (OPEN) establishes state, fires nothing.
        repeat(3) { assertNull(sm.update(Pose.OPEN)) }
        // One CLOSED frame must not fire.
        assertNull(sm.update(Pose.CLOSED))
        assertNull(sm.update(Pose.CLOSED))
        assertEquals(GestureEvent.SEND, sm.update(Pose.CLOSED))

        assertNull(sm.update(Pose.OPEN))
        assertNull(sm.update(Pose.OPEN))
        assertEquals(GestureEvent.RECEIVE, sm.update(Pose.OPEN))
    }

    @Test
    fun lostHandResetsBaseline() {
        // Fist, hand leaves the frame, open palm reappears. That must NOT fire
        // RECEIVE — the baseline was forgotten while the hand was gone.
        val sm = GestureStateMachine(debounce = 1, cooldownMs = 0, lostFrames = 3) { 0L }
        sm.update(Pose.CLOSED)
        repeat(3) { assertNull(sm.update(Pose.NO_HAND)) }
        assertNull(sm.update(Pose.OPEN))
    }

    @Test
    fun briefAmbiguityKeepsBaseline() {
        // A real fist->open passes through ambiguous frames; those are tolerated
        // so the transition still fires.
        val sm = GestureStateMachine(debounce = 1, cooldownMs = 0) { 0L }
        sm.update(Pose.CLOSED)
        sm.update(Pose.UNKNOWN)
        assertEquals(GestureEvent.RECEIVE, sm.update(Pose.OPEN))
    }

    @Test
    fun cooldown() {
        var clock = 0L
        val sm = GestureStateMachine(debounce = 1, cooldownMs = 1000) { clock }
        sm.update(Pose.OPEN)
        assertEquals(GestureEvent.SEND, sm.update(Pose.CLOSED))
        // Immediate re-open is inside the cooldown window -> suppressed (but the
        // pose still flips, so stable is OPEN again).
        assertNull(sm.update(Pose.OPEN))
        clock = 1000
        assertEquals(GestureEvent.SEND, sm.update(Pose.CLOSED))
    }
}
