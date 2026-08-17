"""Local gallery and control panel, installable to an iOS home screen.

Stdlib only. It serves whatever is currently in the output directory — the
renderer writes there on its own schedule and this just reflects it, so neither
needs to know about the other.

Installable as a PWA: Share → Add to Home Screen on iOS gives it an icon and a
full-screen launch with no browser chrome, which is close enough to a native
app for a thing that lives on your wall. A real Swift app would buy push
notifications and widgets, and cost weeks.

The page polls a small JSON endpoint and swaps an <img> only when that design's
mtime has actually changed, so it updates without the whole page flickering.

Note: `serve --host 0.0.0.0` means anything on your network can change which
flight is tracked. That is fine on a home LAN and wrong on a shared one.
"""
from __future__ import annotations

import io
import json
import mimetypes
import time
from datetime import datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import auth, config, pages, palette
from .device import PREFIX as DEVICE_PREFIX
from .device import DeviceAPI
from .display import Selection
from .render import DESIGNS as _REGISTRY
from .tracking import LANDED, OUT_OF_RANGE, Tracker

# (name, title, blurb) triples for the page. Derived from the render registry
# so a new design cannot silently fail to appear here.
DESIGNS = [(d.name, d.title, d.blurb) for d in _REGISTRY]

MANIFEST = {
    "name": "flightframe",
    "short_name": "flightframe",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#DAD8CE",
    "theme_color": "#2F4B7C",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
}

PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="flightframe">
<meta name="theme-color" content="#2F4B7C">
<link rel="manifest" href="/manifest.json">\n<link rel="icon" type="image/png" href="/favicon.png">
<link rel="apple-touch-icon" href="/icon-192.png">
<title>flightframe</title>
<style>
  :root {
    color-scheme: light dark;
    --bg:#f4f3ef; --card:#fff; --fg:#16150f; --muted:#6b6a61; --line:#dedbd2;
    --accent:#2F4B7C; --good:#3f6b4a; --warn:#9c8420; --bad:#9E3B32;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#17171a; --card:#1f1f23; --fg:#ecebe6; --muted:#9a988e;
            --line:#33333a; --accent:#8fb0e0; --good:#7fb08c; --warn:#d4bb52; }
  }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; padding:max(20px, env(safe-area-inset-top)) 16px 60px;
         background:var(--bg); color:var(--fg);
         font:15px/1.6 ui-sans-serif,system-ui,-apple-system,sans-serif; }
  .wrap { max-width:1500px; margin:0 auto; }
  header { display:flex; flex-wrap:wrap; gap:8px 20px; align-items:baseline;
           border-bottom:1px solid var(--line); padding-bottom:14px; margin-bottom:18px; }
  h1 { font-size:19px; font-weight:600; margin:0; }
  .meta { color:var(--muted); font-size:13px; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%;
         background:var(--good); margin-right:7px; vertical-align:1px; }
  .dot.stale{background:var(--warn)} .dot.cold{background:var(--bad)}

  .panel { background:var(--card); border:1px solid var(--line); border-radius:12px;
           padding:15px; margin-bottom:22px; }
  .panel h2 { font-size:14px; font-weight:600; margin:0 0 10px; }
  .row { display:flex; gap:9px; flex-wrap:wrap; }
  input { flex:1 1 150px; min-width:0; font:16px/1.3 inherit; padding:12px 13px;
          border:1px solid var(--line); border-radius:9px; background:var(--bg);
          color:var(--fg); text-transform:uppercase; }
  button { font:15px/1 inherit; font-weight:500; padding:12px 17px; border-radius:9px;
           border:1px solid var(--line); background:var(--bg); color:var(--fg);
           cursor:pointer; }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  button.on { background:var(--accent); border-color:var(--accent); color:#fff; }
  button.next { border-color:var(--accent); color:var(--accent); }
  button.ghost[disabled] { opacity:1; cursor:default; }
  button[hidden] { display:none; }
  figure.showing { border-color:var(--accent); box-shadow:0 0 0 2px var(--accent); }
  .badge { font-size:11px; font-weight:500; color:var(--accent); letter-spacing:.4px; }
  button:active { transform:scale(.98); }
  .note { font-size:13px; color:var(--muted); margin-top:10px; }
  .note.err { color:var(--bad); }
  .tracked { display:flex; gap:12px; align-items:baseline; flex-wrap:wrap;
             font-size:15px; margin-bottom:9px; }
  .tracked b { font-size:20px; font-weight:600; }
  .pill { font-size:12px; font-weight:500; padding:3px 9px; border-radius:99px;
          border:1px solid var(--line); color:var(--muted); }
  .pill.air{color:var(--good);border-color:currentColor}
  .pill.gap{color:var(--warn);border-color:currentColor}
  .pill.down{color:var(--bad);border-color:currentColor}

  .grid { display:grid; gap:18px; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); }
  figure { margin:0; background:var(--card); border:1px solid var(--line);
           border-radius:11px; overflow:hidden; }
  .frame { aspect-ratio:3/4; background:#dad8ce; display:block; }
  .frame img { width:100%; height:100%; object-fit:contain; display:block; }
  .frame.missing { display:grid; place-items:center; color:#6b6a61; font-size:13px; }
  figcaption { padding:12px 14px 14px; border-top:1px solid var(--line); }
  .name { font-weight:600; }
  .desc { color:var(--muted); font-size:12.5px; margin-top:2px; }
  .stamp { color:var(--muted); font-size:12px; margin-top:8px;
           font-variant-numeric:tabular-nums; }
  a.full { color:inherit; text-decoration:none; }
  footer { margin-top:30px; color:var(--muted); font-size:12.5px; }
</style>
</head><body><div class="wrap">

<header>
  <h1>flightframe</h1>
  <div class="meta" id="status">loading…</div>
</header>

<div class="panel">
  <h2>On the frame</h2>
  <div class="row" id="picker"></div>
  <div class="note" id="picknote"></div>
</div>

<div class="panel">
  <h2>Track a flight</h2>
  <div id="tracked"></div>
  <div class="row">
    <input id="flight" placeholder="BA117" autocapitalize="characters"
           autocomplete="off" autocorrect="off" spellcheck="false"
           enterkeyhint="go" aria-label="Flight number">
    <button class="primary" id="go">Track</button>
    <button id="stop">Stop</button>
  </div>
  <div class="note" id="note">Enter the flight number as printed on the ticket.
    It takes over the frame until 30 minutes after landing.</div>
</div>

<div class="grid" id="grid"></div>

<footer>
  Previews use the same six inks as the panel. Each render also writes a
  960,000-byte .bin beside the PNG. Checks for new renders every 5 seconds.
</footer>

</div><script>
const DESIGNS = __DESIGNS__;
const grid = document.getElementById('grid');
const statusEl = document.getElementById('status');
const noteEl = document.getElementById('note');
const trackedEl = document.getElementById('tracked');
const seen = {};

grid.innerHTML = DESIGNS.map(d => `
  <figure id="card-${d[0]}" hidden>
    <a class="full" href="/img/${d[0]}.png" target="_blank" rel="noopener">
      <div class="frame missing" id="f-${d[0]}">not rendered yet</div>
    </a>
    <figcaption>
      <div class="name">${d[1]}</div>
      <div class="desc">${d[2]}</div>
      <div class="badge" id="b-${d[0]}"></div>
      <div class="stamp" id="s-${d[0]}">—</div>
    </figcaption>
  </figure>`).join('');

function ago(s){ if(s<60) return Math.round(s)+'s ago';
  if(s<3600) return Math.round(s/60)+' min ago'; return Math.round(s/3600)+' h ago'; }

async function sendTo(path, body, target) {
  target.className = 'note';
  target.textContent = 'working…';
  try {
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                                 body: JSON.stringify(body||{})});
    const d = await r.json();
    target.textContent = d.message || '';
    if (!d.ok) target.className = 'note err';
    poll();
  } catch (e) {
    target.className = 'note err';
    target.textContent = 'Could not reach the server.';
  }
}

async function send(path, body) {
  noteEl.className = 'note';
  noteEl.textContent = 'working…';
  try {
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                                 body: JSON.stringify(body||{})});
    const d = await r.json();
    noteEl.textContent = d.message || '';
    if (!d.ok) noteEl.className = 'note err';
    poll();
  } catch (e) {
    noteEl.className = 'note err';
    noteEl.textContent = 'Could not reach the server.';
  }
}

function drawPicker(state) {
  const p = document.getElementById('picker');
  if (p.dataset.built !== 'yes') {
    // The tracked flight is a real state of the frame, so it belongs in the
    // picker. It is not selectable here — the flight controls own it — but
    // showing the stored choice as active while a flight is on the wall was
    // simply wrong about what the frame is displaying.
    p.innerHTML = '<button data-design="flight" class="ghost" disabled>' +
                  'Tracked flight</button>' +
      state.selectable.map(n => {
        const d = DESIGNS.find(x => x[0] === n);
        return `<button data-design="${n}">${d ? d[1] : n}</button>`;
      }).join('');
    p.dataset.built = 'yes';
    p.querySelectorAll('button:not([disabled])').forEach(b =>
      b.onclick = () => sendTo('/api/design', {design: b.dataset.design},
                               document.getElementById('picknote')));
  }
  const flightBtn = p.querySelector('[data-design="flight"]');
  flightBtn.hidden = !state.tracking;
  p.querySelectorAll('button').forEach(b => {
    const isShowing = b.dataset.design === state.showing;
    const isNext = !!state.tracking && b.dataset.design === state.selected;
    b.className = isShowing ? 'on' : (isNext ? 'next' : '');
  });
  const sel = DESIGNS.find(x => x[0] === state.selected);
  document.getElementById('picknote').textContent = state.tracking
    ? `${state.tracking.label} is on the frame; it returns to ` +
      `${sel ? sel[1] : state.selected} 30 minutes after landing.`
    : 'The frame shows this until you change it.';
}

document.getElementById('go').onclick = () =>
  send('/api/track', {flight: document.getElementById('flight').value});
document.getElementById('stop').onclick = () => send('/api/untrack');
document.getElementById('flight').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('go').click();
});

function renderTracked(t) {
  if (!t) { trackedEl.innerHTML = ''; return; }
  const cls = t.status === 'airborne' ? 'air'
            : t.status === 'out_of_range' ? 'gap'
            : t.status === 'landed' ? 'down' : '';
  const bits = [];
  if (t.progress != null) bits.push(Math.round(t.progress*100) + '% flown');
  if (t.eta_minutes != null && t.status !== 'landed') {
    const m = t.eta_minutes;
    bits.push(m < 60 ? Math.round(m)+' min left'
      : Math.floor(m/60)+'h '+String(Math.round(m%60)).padStart(2,'0')+'m left');
  }
  if (t.panel_interval_s) bits.push('frame every '+Math.round(t.panel_interval_s/60)+' min');
  trackedEl.innerHTML = `<div class="tracked">
    <b>${t.label}</b><span>${t.route}</span>
    <span class="pill ${cls}">${t.status_text}</span>
    <span class="meta">${bits.join(' · ')}</span></div>`;
}

async function poll() {
  let state;
  try { state = await (await fetch('/api/state',{cache:'no-store'})).json(); }
  catch(e){ statusEl.innerHTML = '<span class="dot cold"></span>server unreachable'; return; }

  renderTracked(state.tracking);
  drawPicker(state);

  let newest = Infinity;
  for (const d of state.designs) {
    const card = document.getElementById('card-' + d.name);
    card.hidden = !d.exists;
    if (!d.exists) continue;
    const on = d.name === state.showing;
    card.className = on ? 'showing' : '';
    document.getElementById('b-' + d.name).textContent = on ? 'ON THE FRAME' : '';
    if (!d.on_demand) newest = Math.min(newest, d.age);
    if (seen[d.name] !== d.mtime) {
      seen[d.name] = d.mtime;
      const box = document.getElementById('f-' + d.name);
      box.className = 'frame';
      box.innerHTML = `<img alt="${d.name} preview" src="/img/${d.name}.png?v=${d.mtime}">`;
    }
    document.getElementById('s-'+d.name).textContent =
      `${d.clock} · ${ago(d.age)} · ${d.kb} kB`;
  }
  const cls = newest < 150 ? '' : (newest < 900 ? 'stale' : 'cold');
  statusEl.innerHTML = `<span class="dot ${cls}"></span>${state.label} · newest render ` +
    (newest === Infinity ? 'none' : ago(newest));
}

poll();
setInterval(poll, 5000);
</script></body></html>
"""

STATUS_TEXT = {
    "scheduled": "not yet airborne",
    "airborne": "in the air",
    OUT_OF_RANGE: "out of range",
    LANDED: "landed",
}


def _icon(size: int) -> bytes:
    """The mark: a top-view airliner climbing across the poster paper.

    Same visual language as the glass — an ink silhouette on paper, nothing
    else. Drawn at 4x and downsampled so it stays crisp at favicon sizes.
    """
    import math

    from PIL import Image, ImageDraw
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    paper = palette.RGB["white"]
    navy = (0x2F, 0x4B, 0x7C)
    red = palette.RGB["red"]
    ink = palette.RGB["black"]

    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=s * 0.22, fill=paper,
                        outline=ink, width=max(4, s // 44))

    # Airliner top view, nose-up in unit coords (x, y), y down; right half —
    # mirrored for the left. Proportions eyeballed from the shape library.
    half = [
        (0.000, -0.460),           # nose tip
        (0.052, -0.380),
        (0.060, -0.130),           # fuselage at wing root
        (0.420,  0.100),           # wing leading edge to tip
        (0.420,  0.170),           # wing tip chord
        (0.066,  0.080),           # trailing edge back to fuselage
        (0.058,  0.300),           # rear fuselage
        (0.190,  0.410),           # tailplane tip
        (0.190,  0.462),
        (0.040,  0.410),           # tail root
        (0.000,  0.470),           # tail end
    ]
    outline = half + [(-x, y) for x, y in reversed(half)]

    angle = math.radians(38)       # climbing to the north-east
    ca, sa = math.cos(angle), math.sin(angle)
    cx, cy, scale = s * 0.55, s * 0.46, s * 0.70
    pts = [(cx + (x * ca - y * sa) * scale, cy + (x * sa + y * ca) * scale)
           for x, y in outline]
    d.polygon(pts, fill=navy)

    # Two fading contrail dashes trailing behind the tail.
    nose = (math.sin(math.radians(38)), -math.cos(math.radians(38)))
    for gap, ln, w in [(0.56, 0.13, 0.050), (0.78, 0.08, 0.038)]:
        x0 = cx - nose[0] * scale * gap
        y0 = cy - nose[1] * scale * gap
        x1 = cx - nose[0] * scale * (gap + ln)
        y1 = cy - nose[1] * scale * (gap + ln)
        d.line([x0, y0, x1, y1], fill=red, width=int(s * w))

    img = img.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


MAX_BODY = 64 * 1024      # public internet: nothing here needs a bigger POST

SESSION_COOKIE = "ff_session"


class Handler(BaseHTTPRequestHandler):
    server_version = "flightframe"
    timeout = 30

    def __init__(self, *args, app, registry, device: DeviceAPI,
                 limiter, **kw):
        self.app = app
        self.registry = registry
        self.device = device
        self.limiter = limiter
        super().__init__(*args, **kw)

    def log_message(self, fmt, *args):
        pass

    # -- identity ---------------------------------------------------------
    #
    # Tenant identity comes from exactly two places: the session cookie for
    # humans, the bearer token for frames. Nothing a client sends in a path,
    # query, or body ever selects a tenant.

    def _session(self):
        cookie = self.headers.get("Cookie") or ""
        token = ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                token = value
        return self.registry.session_user(token) if token else None

    def _tenant_ctx(self, user):
        """Per-request tenant context: settings + the Path-scoped helpers."""
        settings = config.for_tenant(self.app, user["tenant"])
        tracker = Tracker(settings.data_dir, settings.cache_dir,
                          settings.user_agent)
        return settings, Selection(settings.data_dir), tracker

    def _deny(self, status: int = 401) -> None:
        if self.command == "GET" and not self.path.startswith("/api"):
            self.send_response(303)
            self.send_header("Location", "/login")
            self.end_headers()
        else:
            self._json({"ok": False, "message": "sign in required"}, status)

    def _origin_ok(self) -> bool:
        return auth.origin_ok(self.headers, set(self.app.app_hosts))

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(json.dumps(payload).encode("utf-8"), "application/json", status)

    # -- GET --------------------------------------------------------------

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        # public routes: nothing tenant-shaped is reachable without identity
        if path == "/login":
            self._send(pages.LOGIN_PAGE.encode(), "text/html; charset=utf-8")
            return
        if path == "/manifest.json":
            self._json(MANIFEST)
            return
        if path in ("/icon-192.png", "/icon-512.png", "/apple-touch-icon.png",
                    "/favicon.ico", "/favicon.png"):
            self._send(_icon(512 if "512" in path else 192), "image/png")
            return
        if path == "/healthz":
            self._json({"ok": True})
            return

        # device routes authenticate with the bearer token inside DeviceAPI
        if path.startswith(f"{DEVICE_PREFIX}/img/"):
            self._device_image(Path(path).name)
            return
        if path == f"{DEVICE_PREFIX}/display":
            self._device_display()
            return

        # everything else needs a session — fail closed
        user = self._session()
        if user is None:
            self._deny()
            return
        if path in ("/", "/index.html"):
            page = PAGE.replace("__DESIGNS__", json.dumps(DESIGNS))
            page = page.replace("</body>", pages.SETTINGS_SNIPPET + "</body>")
            self._send(page.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(self._state(user))
        elif path == "/api/flights":
            self._json({"flights": self.registry.flights_for(
                user["tenant"]["id"], resolve_follow=True,
                include_done=True),
                "own": not user["tenant"].get("follows_flights_of")})
        elif path == "/api/settings":
            tenant = user["tenant"]
            self._json({k: tenant[k] for k in
                        ("label", "lat", "lon", "radius_nm", "units",
                         "refresh_minutes", "awake_from", "awake_until", "tz")})
        elif path.startswith("/img/"):
            self._image(user, Path(path).name)
        else:
            self._send(b"not found", "text/plain", 404)

    def _image(self, user, name: str) -> None:
        if name not in {f"{d[0]}.png" for d in DESIGNS}:
            self._send(b"not found", "text/plain", 404)
            return
        target = self.app.tenant_out(user["tenant"]["id"]) / name
        if not target.exists():
            self._send(b"not rendered yet", "text/plain", 404)
            return
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self._send(target.read_bytes(), ctype)

    def do_HEAD(self) -> None:
        """Headers only.

        The Gateway dashboard pings every service with a HEAD request to drive
        its status dot. BaseHTTPRequestHandler answers 501 for anything it does
        not implement, which the dashboard's no-cors fetch cannot distinguish
        from a healthy reply — so the dot would read green even with the
        service broken.
        """
        path = self.path.split("?", 1)[0]
        ok = (path in ("/", "/index.html", "/manifest.json", "/api/state")
              or path.startswith("/img/") or path.endswith(".png"))
        self.send_response(200 if ok else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8"
                         if ok else "text/plain")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    # -- device protocol ---------------------------------------------------

    def _token(self) -> str:
        auth = self.headers.get("Authorization") or ""
        return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

    def _base_url(self) -> str:
        host = self.headers.get("Host") or "127.0.0.1"
        # Behind Caddy the socket is plain HTTP but the frame must be given
        # back an https URL, or its next download breaks the CA rule.
        proto = self.headers.get("X-Forwarded-Proto") or "http"
        return f"{proto}://{host}"

    def _device_display(self) -> None:
        headers = {k.lower(): v for k, v in self.headers.items()}
        status, payload = self.device.display(self._token(), headers,
                                              self._base_url())
        if status == 200:
            print(f"frame -> {payload['_design']} ({payload['_why']}), "
                  f"sleep {payload['sleep_s']}s", flush=True)
        self._json(payload, status)

    def _device_image(self, name: str) -> None:
        if not name.endswith(".bin"):
            self._send(b"not found", "text/plain", 404)
            return
        data = self.device.image_by_hash(self._token(), name[:-4])
        if data is None:
            # The poster changed between the display call and the download.
            # The frame treats any non-200 as a failed wake and retries, which
            # is exactly right — it will pick up the new hash next time.
            self._send(b"unknown image", "text/plain", 404)
            return
        self._send(data, "application/octet-stream")

    # -- POST -------------------------------------------------------------

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0:
                # int("-1") slips past the > MAX_BODY guard, and read(-1)
                # blocks until EOF — a slow-loris on kept-alive sockets.
                self._json({"ok": False, "message": "bad length"}, 400)
                return
            if length > MAX_BODY:
                self._json({"ok": False, "message": "body too large"}, 413)
                return
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            body = {}
        if not isinstance(body, dict):
            # A bare int/list/string is valid JSON but every handler calls
            # body.get(...); coerce to empty so none of them raise.
            body = {}

        # device routes: bearer-token identity, no cookies, no Origin check
        if path == f"{DEVICE_PREFIX}/setup":
            status, payload = self.device.setup(body)
            print(f"frame setup: {payload.get('error') or 'token issued'}",
                  flush=True)
            self._json(payload, status)
            return
        if path == f"{DEVICE_PREFIX}/log":
            status, payload = self.device.log(self._token(), body)
            self._json(payload, status)
            return

        # login: public, rate-limited, no session yet
        if path == "/login":
            # The socket peer only. CF-Connecting-IP is client-supplied and
            # this hostname is DNS-only (no Cloudflare in front to set it
            # authentically), so trusting it would let one attacker mint a
            # fresh limiter bucket per request and brute-force freely.
            ip = self.client_address[0]
            if not self.limiter.allow(ip):
                self._json({"ok": False, "message": "too many attempts; "
                            "try again in a few minutes"}, 429)
                return
            record = self.registry.user_by_email(str(body.get("email", "")))
            password = str(body.get("password", ""))
            if not record:
                # Spend the same scrypt time on a miss, so response latency
                # cannot distinguish a registered email from an unknown one.
                auth.verify_password(password, auth.DUMMY_HASH)
            if record and auth.verify_password(password, record["pw_hash"]):
                token = self.registry.session_create(record["id"])
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header(
                    "Set-Cookie",
                    f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; "
                    f"Secure; SameSite=Lax; Max-Age={30*86400}")
                out = json.dumps({"ok": True}).encode()
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            else:
                self._json({"ok": False, "message": "wrong email or password"},
                           401)
            return

        # everything below changes tenant state: session + Origin required
        user = self._session()
        if user is None:
            self._deny()
            return
        if not self._origin_ok():
            self._json({"ok": False, "message": "cross-origin request "
                        "refused"}, 403)
            return
        settings, selection, tracker = self._tenant_ctx(user)

        if path == "/logout":
            cookie = self.headers.get("Cookie") or ""
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == SESSION_COOKIE:
                    self.registry.session_destroy(value)
            self._json({"ok": True})
        elif path == "/api/track":
            flight, message = tracker.start(str(body.get("flight", "")))
            if flight is not None:
                tracker.poll()
            self._json({"ok": flight is not None, "message": message})
        elif path == "/api/design":
            ok, message = selection.set(str(body.get("design", "")))
            self._json({"ok": ok, "message": message})
        elif path == "/api/untrack":
            tracker.clear()
            self._json({"ok": True, "message": "Tracking stopped."})
        elif path == "/api/settings":
            self._json(self._save_settings(user, body))
        elif path == "/api/flights":
            self._json(self._save_flight(user, body))
        elif path == "/api/flights/delete":
            try:
                fid = int(body.get("id") or 0)
            except (TypeError, ValueError):
                fid = 0
            self._json({"ok": self.registry.flight_delete(
                user["tenant"]["id"], fid)})
        else:
            self._json({"ok": False, "message": "unknown endpoint"}, 404)

    def _save_flight(self, user, body: dict) -> dict:
        import re as _re
        from datetime import date as _date
        flight_no = str(body.get("flight_no", "")).strip().upper()
        if not _re.fullmatch(r"[A-Z0-9]{3,8}", flight_no):
            return {"ok": False, "message":
                    "flight number as printed on the ticket, e.g. BA117"}
        try:
            when = _date.fromisoformat(str(body.get("date", "")))
        except ValueError:
            return {"ok": False, "message": "date must be YYYY-MM-DD"}
        if when < _date.today():
            return {"ok": False, "message": "that date is in the past"}
        extra = {}
        if body.get("dep_time"):
            if not _re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d",
                                 str(body["dep_time"])):
                return {"ok": False, "message": "departure time must be HH:MM"}
            extra["dep_time"] = str(body["dep_time"])
        for key, cap in (("origin", 32), ("destination", 32),
                         ("aircraft", 32), ("note", 80)):
            if body.get(key):
                extra[key] = str(body[key]).strip()[:cap]
        if not (extra.get("origin") and extra.get("destination")):
            # Route is keyless and near-static: fill it from the flight
            # number so nobody has to know their IATA codes.
            from . import schedule
            auto = schedule.route_autofill(flight_no, self.app.cache_dir,
                                           self.app.user_agent)
            for key in ("origin", "destination"):
                extra.setdefault(key, auto.get(key))
            extra = {k: v for k, v in extra.items() if v}
        fid = self.registry.flight_add(user["tenant"]["id"], flight_no,
                                       when.isoformat(), **extra)
        return {"ok": True, "id": fid}

    def _save_settings(self, user, body: dict) -> dict:
        """Validate every field the dashboard can send. The browser mirrors
        these rules for friendliness; this is the enforcement."""
        import re
        fields: dict = {}
        try:
            for key in ("lat", "lon", "radius_nm"):
                if key in body:
                    fields[key] = float(body[key])
                    if fields[key] != fields[key]:      # NaN
                        raise ValueError(f"{key} is not a number")
            if "lat" in fields and not (-90 <= fields["lat"] <= 90):
                raise ValueError("latitude must be between -90 and 90")
            if "lon" in fields and not (-180 <= fields["lon"] <= 180):
                raise ValueError("longitude must be between -180 and 180")
            if "radius_nm" in fields and not (5 <= fields["radius_nm"] <= 250):
                raise ValueError("radius must be 5–250 nm")
            if "refresh_minutes" in body:
                v = int(body["refresh_minutes"])
                if not (3 <= v <= 360):
                    raise ValueError("refresh must be 3–360 minutes")
                fields["refresh_minutes"] = v
            if "label" in body:
                label = str(body["label"]).strip()
                if not (1 <= len(label) <= 40):
                    raise ValueError("label must be 1–40 characters")
                fields["label"] = label
            for key in ("awake_from", "awake_until"):
                if key in body:
                    v = str(body[key])
                    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
                        raise ValueError(f"{key.replace('_', ' ')} must be HH:MM")
                    fields[key] = v
            if ("awake_from" in fields) != ("awake_until" in fields):
                raise ValueError("set both awake times together")
            if "awake_from" in fields and fields["awake_from"] >= fields["awake_until"]:
                raise ValueError("awake window must start before it ends")
            if "units" in body:
                if body["units"] not in ("metric", "aviation"):
                    raise ValueError("units must be metric or aviation")
                fields["units"] = body["units"]
            if "tz" in body:
                from zoneinfo import ZoneInfo
                tz = str(body["tz"]).strip()
                try:
                    ZoneInfo(tz)
                except Exception:
                    raise ValueError(f"unknown timezone {tz!r} — use an IANA "
                                     "name like Europe/London") from None
                fields["tz"] = tz
        except ValueError as exc:
            return {"ok": False, "message": str(exc)}
        except (TypeError, KeyError):
            return {"ok": False, "message": "malformed settings payload"}
        self.registry.tenant_update(user["tenant"]["id"], fields)
        return {"ok": True}

    # -- state ------------------------------------------------------------

    def _state(self, user) -> dict:
        settings, selection, tracker = self._tenant_ctx(user)
        out_dir = settings.out_dir
        now = time.time()
        designs = []
        for entry in _REGISTRY:
            png = out_dir / f"{entry.name}.png"
            row = {"name": entry.name, "on_demand": entry.on_demand, "exists": False}
            if png.exists():
                st = png.stat()
                row.update(exists=True, mtime=int(st.st_mtime),
                           age=max(0.0, now - st.st_mtime),
                           clock=datetime.fromtimestamp(st.st_mtime).strftime("%H:%M:%S"),
                           kb=round(st.st_size / 1024))
            designs.append(row)

        tracking = None
        flight = tracker.load()
        if flight is not None and flight.status != "expired":
            o = (flight.origin or {}).get("iata") or "???"
            d = (flight.destination or {}).get("iata") or "???"
            tracking = {
                "label": flight.callsign_iata or flight.callsign,
                "route": f"{o} to {d}",
                "status": flight.status,
                "status_text": STATUS_TEXT.get(flight.status, flight.status),
                "progress": flight.progress,
                "eta_minutes": flight.eta_minutes,
                "panel_interval_s": flight.panel_interval_s,
            }

        return {"designs": designs, "label": user["tenant"]["label"],
                "radius": round(settings.radius_nm), "tracking": tracking,
                "selected": selection.current(),
                "showing": selection.effective(tracking is not None),
                "selectable": selection.selectable()}


def serve(app, registry, host: str, port: int) -> None:
    device = DeviceAPI(app, registry)
    handler = partial(Handler, app=app, registry=registry, device=device,
                      limiter=auth.LoginLimiter())
    httpd = ThreadingHTTPServer((host, port), handler)
    shown = "localhost" if host in ("127.0.0.1", "localhost") else host
    print(f"flightframe on http://{shown}:{port} "
          f"({len(registry.tenants(status=None))} tenants)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
