// Reports what's on THIS page: what you selected, the image you're pointing at,
// and how far into a video you are. These are the things the daemon can't get
// from outside the browser — the address bar knows none of them.

// Where the cursor is. You point the mouse at something, take your hand off the
// mouse, and gesture at the camera — the pointer stays put, so this still says
// what you were pointing at.
let mouse = null;

document.addEventListener("mousemove", (e) => {
  mouse = { x: e.clientX, y: e.clientY };
}, true);

function hoveredImage() {
  // Deliberately read what's under the cursor NOW, rather than remembering the
  // last image you ever touched. Remembering would send a stale image long
  // after you'd moved on — the same trap as sending an old clipboard.
  if (!mouse) return null;
  // elementsFromPoint (plural) returns the whole stack, so an overlay sitting
  // on top of a thumbnail doesn't hide the image underneath it.
  for (const el of document.elementsFromPoint(mouse.x, mouse.y) || []) {
    if (el.tagName === "IMG" && el.currentSrc) return el.currentSrc;
  }
  return null;
}

function videoSeconds() {
  const vids = Array.from(document.querySelectorAll("video"));
  const v = vids.find((x) => !x.paused && x.currentTime > 0) || vids[0];
  // Under ~5s isn't a deliberate timestamp, it's just the start of the video.
  if (!v || !v.currentTime || v.currentTime < 5) return null;
  return Math.floor(v.currentTime);
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.kind !== "query") return;
  reply({
    url: location.href,
    title: document.title,
    selection: (window.getSelection() || "").toString().trim(),
    image: hoveredImage(),
    videoTime: videoSeconds(),
  });
  return true;
});
