package com.yoink.net

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicBoolean

/**
 * mDNS peer discovery over NSD, browse-only.
 *
 * The desktop advertises `_yoink._tcp` with a TXT record carrying its stable
 * `device` id and friendly `name` (daemon/net/discovery.py). We only browse:
 * the phone is client-only, so it never advertises and the desktop never dials
 * it.
 *
 * The service type must match the Python side exactly. Python registers the
 * fully-qualified `_yoink._tcp.local.`; NsdManager wants it without the domain.
 *
 * Two ids are in play and they are NOT the same thing: the mDNS *instance
 * label* changes on every daemon launch, while the *device id* in the TXT
 * record is stable across restarts — pairing keys on the latter.
 */
class Discovery(
    private val context: Context,
    private val onPeerUp: (deviceId: String, host: String, port: Int) -> Unit,
    private val onPeerDown: (deviceId: String) -> Unit,
    private val log: (String) -> Unit,
) {
    private val nsd by lazy { context.getSystemService(NsdManager::class.java) }

    /** mDNS service name -> device id, so a removal can be mapped back. */
    private val seen = mutableMapOf<String, String>()

    // NsdManager historically handles one resolve at a time and answers a
    // second concurrent call with FAILURE_ALREADY_ACTIVE, so resolves are
    // queued rather than fired in parallel.
    private val pending = ConcurrentLinkedQueue<NsdServiceInfo>()
    private val resolving = AtomicBoolean(false)

    private var listener: NsdManager.DiscoveryListener? = null

    fun start() {
        val l = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {
                log("discovery on, browsing for $SERVICE_TYPE")
            }

            override fun onServiceFound(info: NsdServiceInfo) {
                pending.add(info)
                pumpResolve()
            }

            override fun onServiceLost(info: NsdServiceInfo) {
                // Loss carries no TXT record, so map the service name back to
                // the device id recorded when it appeared.
                seen.remove(info.serviceName)?.let(onPeerDown)
            }

            override fun onDiscoveryStopped(serviceType: String) {}

            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                log("discovery failed to start ($errorCode)")
            }

            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
        }
        listener = l
        nsd.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, l)
    }

    private fun pumpResolve() {
        if (!resolving.compareAndSet(false, true)) return
        val next = pending.poll()
        if (next == null) {
            resolving.set(false)
            return
        }
        @Suppress("DEPRECATION") // registerServiceInfoCallback needs API 34; minSdk is 26
        nsd.resolveService(next, object : NsdManager.ResolveListener {
            override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {
                log("resolve failed for ${info.serviceName} ($errorCode)")
                resolving.set(false)
                pumpResolve()
            }

            override fun onServiceResolved(info: NsdServiceInfo) {
                handleResolved(info)
                resolving.set(false)
                pumpResolve()
            }
        })
    }

    private fun handleResolved(info: NsdServiceInfo) {
        val deviceId = info.attributes["device"]?.toString(Charsets.UTF_8)
        @Suppress("DEPRECATION") // hostAddresses needs API 34
        val host = info.host?.hostAddress
        if (deviceId.isNullOrEmpty() || host.isNullOrEmpty()) {
            log("ignoring ${info.serviceName}: no device id or address")
            return
        }
        seen[info.serviceName] = deviceId
        onPeerUp(deviceId, host, info.port)
    }

    fun stop() {
        listener?.let {
            try {
                nsd.stopServiceDiscovery(it)
            } catch (e: IllegalArgumentException) {
                // already stopped; nothing to unwind
            }
        }
        listener = null
    }

    companion object {
        const val SERVICE_TYPE = "_yoink._tcp"
    }
}
