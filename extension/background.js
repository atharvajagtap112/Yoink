// Holds the ws://localhost link to the Yoink daemon and answers its "what's in
// the browser right now?" query with the active tab's details.
//
// ponytail: MV3 kills an idle service worker, so this can be asleep when the
// daemon asks. That's survivable by design — the daemon just falls back to
// reading the address bar. The alarm below wakes us to reconnect. If it ever
// proves too flaky, the upgrade path is chrome.offscreen (a persistent doc).

const PORT = 8777;
let ws = null;

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN ||
             ws.readyState === WebSocket.CONNECTING)) {
    return;                                   // already linked
  }
  try {
    ws = new WebSocket(`ws://localhost:${PORT}`);
  } catch (e) {
    ws = null;
    return;                                   // daemon isn't running; retry later
  }
  ws.onmessage = async (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (e) {
      return;
    }
    if (msg.kind !== "query") return;
    try {
      ws.send(JSON.stringify(await describeActiveTab()));
    } catch (e) {
      // socket died mid-answer; the daemon's timeout covers this
    }
  };
  ws.onclose = () => { ws = null; };
  ws.onerror = () => { ws = null; };
}

async function describeActiveTab() {
  // Only the browser you're actually LOOKING at may answer. Chrome, Edge, Opera
  // and Brave can all be connected at once, and an unfocused one happily
  // reporting its own active tab is how a background video got sent instead of
  // the PDF that was on screen in another browser.
  let win = null;
  try {
    win = await chrome.windows.getLastFocused();
  } catch (e) {
    // treated as unfocused below
  }
  if (!win || !win.focused) return { focused: false };

  const [tab] = await chrome.tabs.query({ active: true, windowId: win.id });
  if (!tab) return { focused: false };
  const base = { focused: true, url: tab.url || "", title: tab.title || "" };
  try {
    const reply = await chrome.tabs.sendMessage(tab.id, { kind: "query" });
    return Object.assign(base, reply || {});
  } catch (e) {
    // Tabs that were already open when the extension loaded never got a content
    // script, so the query above throws and we'd silently lose the selection and
    // the video timestamp. Inject it now and ask again rather than making you
    // reload every tab. (Hover history starts empty, so the hovered image only
    // works from the next mouse move on this tab.)
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content.js"],
      });
      const reply = await chrome.tabs.sendMessage(tab.id, { kind: "query" });
      return Object.assign(base, reply || {});
    } catch (e2) {
      // chrome:// page, the built-in PDF viewer, the web store: no scripts
      // allowed. The tab URL alone is still worth sending.
      return base;
    }
  }
}

connect();
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.alarms.create("yoink-reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(connect);
