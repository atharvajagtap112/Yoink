package com.yoink.gesture

import android.os.SystemClock

/**
 * Turns a stream of per-frame poses into SEND / RECEIVE events.
 *
 * Port of daemon/gesture/state_machine.py — same debounce and cooldown, so the
 * gesture feels identical on both devices.
 *
 * Fires on the transition between *stable* poses, never on a held pose:
 *     stable OPEN   -> CLOSED = SEND
 *     stable CLOSED -> OPEN   = RECEIVE
 *
 * Two kinds of "not a pose" are handled differently, and the difference matters:
 *   UNKNOWN  = a hand is visible but mid-motion/ambiguous. A real fist->open
 *              passes through these, so the last stable pose is kept.
 *   NO_HAND  = no hand at all. After it's been gone a moment the baseline is
 *              forgotten, so dropping your hand and raising an open palm doesn't
 *              read as CLOSED->OPEN and fire a phantom RECEIVE.
 */

enum class GestureEvent { SEND, RECEIVE }

const val DEBOUNCE_FRAMES = 4
const val COOLDOWN_MS = 800L
const val LOST_HAND_FRAMES = 8

class GestureStateMachine(
    private val debounce: Int = DEBOUNCE_FRAMES,
    private val cooldownMs: Long = COOLDOWN_MS,
    private val lostFrames: Int = LOST_HAND_FRAMES,
    // Injected so the state machine is testable as a plain JVM unit test,
    // with no Android runtime and no sleeping.
    private val now: () -> Long = { SystemClock.elapsedRealtime() },
) {
    /** Last accepted stable pose: OPEN or CLOSED. */
    var stable: Pose? = null
        private set

    private var candidate: Pose? = null
    private var count = 0
    private var absent = 0
    private var lastFire = 0L
    private var fired = false

    /** Feed one frame's pose. Returns the fired event, or null. */
    fun update(pose: Pose): GestureEvent? {
        if (pose == Pose.NO_HAND) {
            candidate = null
            count = 0
            absent++
            if (absent >= lostFrames) stable = null // fresh start next time
            return null
        }
        absent = 0

        if (pose == Pose.UNKNOWN) {
            candidate = null
            count = 0
            return null
        }

        if (pose == candidate) count++ else { candidate = pose; count = 1 }

        if (count < debounce || pose == stable) return null

        // Candidate has held long enough: it's the new stable pose.
        val prev = stable
        stable = pose
        if (prev == null) return null // nothing to transition from

        if (fired && now() - lastFire < cooldownMs) return null

        val event = when {
            prev == Pose.OPEN && pose == Pose.CLOSED -> GestureEvent.SEND
            prev == Pose.CLOSED && pose == Pose.OPEN -> GestureEvent.RECEIVE
            else -> null
        }
        if (event != null) {
            lastFire = now()
            fired = true
        }
        return event
    }
}
