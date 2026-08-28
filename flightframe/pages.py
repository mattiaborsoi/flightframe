"""New HTML surfaces for the multi-tenant dashboard: login and settings.

The main gallery PAGE stays in web.py — it predates tenancy and moving 260
working lines would be churn without benefit. These are the additions.
"""

LOGIN_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="/favicon.png">
<title>flightframe</title>
<style>
  :root { color-scheme: light dark;
          --bg:#f4f3ef; --card:#fff; --fg:#16150f; --muted:#6b6a61;
          --line:#dedbd2; --accent:#2F4B7C; --bad:#9E3B32; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#17171a; --card:#1f1f23; --fg:#ecebe6; --muted:#9a988e;
            --line:#33333a; --accent:#8fb0e0; --bad:#e0837a; }
  }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center;
         background:var(--bg); color:var(--fg); padding:24px;
         font:16px/1.5 ui-sans-serif,system-ui,-apple-system,sans-serif; }
  .card { width:min(380px, 100%); background:var(--card);
          border:1px solid var(--line); border-radius:16px; overflow:hidden; }
  /* the six inks of the glass, as a hairline across the top of the card */
  .inks { display:flex; height:5px; }
  .inks i { flex:1; }
  .inks i:nth-child(1){background:#16150f} .inks i:nth-child(2){background:#9E3B32}
  .inks i:nth-child(3){background:#c9a227} .inks i:nth-child(4){background:#3f6b4a}
  .inks i:nth-child(5){background:#2F4B7C} .inks i:nth-child(6){background:#DAD8CE}
  .inner { padding:30px 28px 26px; }
  .brand { display:flex; align-items:center; gap:13px; margin-bottom:6px; }
  .brand img { width:44px; height:44px; border-radius:10px; }
  h1 { font-size:22px; font-weight:650; margin:0; letter-spacing:.2px; }
  p.sub { color:var(--muted); margin:0 0 24px; font-size:14px; }
  label { display:block; font-size:12px; font-weight:600; color:var(--muted);
          margin:15px 0 5px; text-transform:uppercase; letter-spacing:.07em; }
  input { width:100%; font-size:16px; padding:11px 13px;
          border:1px solid var(--line); border-radius:10px;
          background:var(--bg); color:var(--fg); }
  input:focus { outline:2px solid var(--accent); outline-offset:0;
                border-color:transparent; }
  button { margin-top:22px; width:100%; font-size:16px; font-weight:600;
           padding:12px; border:none; border-radius:10px;
           background:var(--accent); color:#fff; cursor:pointer; }
  button:active { transform:scale(.99); }
  .err { color:var(--bad); font-size:14px; min-height:21px; margin-top:12px; }
  footer { margin-top:18px; color:var(--muted); font-size:12px;
           text-align:center; }
</style></head><body>
<form class="card" id="f">
  <div class="inks"><i></i><i></i><i></i><i></i><i></i><i></i></div>
  <div class="inner">
    <div class="brand">
      <img src="/favicon.png" alt="">
      <h1>flightframe</h1>
    </div>
    <p class="sub">the sky above your house, on your wall</p>
    <label for="email">email</label>
    <input id="email" name="email" type="email" autocomplete="username"
           required autofocus>
    <label for="pw">password</label>
    <input id="pw" name="pw" type="password" autocomplete="current-password"
           required>
    <button>Sign in</button>
    <div class="err" id="err"></div>
    <footer>invite-only &middot; ask the person who built your frame</footer>
  </div>
</form>
<script>
document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await fetch("/login", {method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({email: email.value, password: pw.value})});
  const j = await r.json();
  if (j.ok) location.href = "/";
  else document.getElementById("err").textContent = j.message || "sign-in failed";
});
</script></body></html>"""


# Injected into the gallery PAGE before </body>: a settings card with inline
# tips, client-side validation mirroring the server's, and a geolocation
# helper. No external services: the browser's own position API fills the
# coordinates, which is exactly right for configuring a frame from the room
# it hangs in.
SETTINGS_SNIPPET = """
<style>
  #flights table { width:100%; border-collapse:collapse; margin-bottom:14px; }
  #flights td, #flights th { text-align:left; padding:7px 10px 7px 0;
      border-bottom:1px solid var(--line); font-size:14px; }
  #flights th { font-size:12px; color:var(--muted); font-weight:600;
      text-transform:uppercase; letter-spacing:.05em; }
  #flights .st { font-size:12px; color:var(--muted); }
  #flights .del { padding:5px 10px; font-size:13px; }
  #flights .add { display:grid; grid-template-columns:repeat(4, 1fr);
      gap:9px; }
  #flights .add input { flex:initial; width:100%; text-transform:none;
      font:15px/1.3 inherit; padding:9px 11px; border:1px solid var(--line);
      border-radius:9px; background:var(--bg); color:var(--fg); }
  @media (max-width:640px) { #flights .add { grid-template-columns:1fr 1fr; } }
</style>
<section class="panel" id="flights">
  <h2 id="fl_title">My flights</h2>
  <div class="note" id="fl_follow" hidden>This frame shares its flight list with the traveller's
    travels; the list is managed from their account.</div>
  <table id="fl_table" hidden>
    <thead><tr><th>Date</th><th>Flight</th><th>Route</th><th>Aircraft</th>
      <th></th><th></th></tr></thead>
    <tbody id="fl_rows"></tbody>
  </table>
  <div class="add" id="fl_add">
    <input id="f_no" placeholder="BA117" autocapitalize="characters">
    <input id="f_date" type="date">
    <input id="f_time" type="time" title="departure (optional)">
    <input id="f_aircraft" placeholder="A350-900 (optional)">
    <input id="f_from" placeholder="from: LHR">
    <input id="f_to" placeholder="to: FCO">
    <input id="f_note" placeholder="note (optional)">
    <button class="primary" id="f_save">Add flight</button>
  </div>
  <div class="note" id="fl_msg">On the flight's day the frame starts tracking
    it live automatically; afterwards it returns to the schedule.</div>
</section>
<style>
  /* Settings card speaks the page's own design system: theme variables for
     dark/light, .panel chrome, and it must undo two global input rules —
     flex-grow (fields ballooned to full width) and uppercase. */
  #settings { max-width:680px; }
  #settings .fields { display:grid; grid-template-columns:1fr 1fr;
                      gap:14px 18px; }
  #settings .field.wide { grid-column:1 / -1; }
  @media (max-width:560px) { #settings .fields { grid-template-columns:1fr; }
                             #settings .field.wide { grid-column:auto; } }
  #settings label { display:block; font-size:13px; font-weight:600;
                    margin-bottom:2px; }
  #settings .tip { display:block; font-size:12px; color:var(--muted);
                   line-height:1.45; margin-bottom:6px; }
  #settings input, #settings select {
      flex:initial; width:100%; text-transform:none; font:15px/1.3 inherit;
      padding:9px 11px; border:1px solid var(--line); border-radius:9px;
      background:var(--bg); color:var(--fg); }
  #settings input:invalid { border-color:var(--bad); }
  #settings .pair { display:flex; gap:9px; align-items:center; }
  #settings .pair input { flex:1 1 0; }
  #settings .pair button { flex:0 0 auto; white-space:nowrap; }
  #settings .actions { margin-top:16px; display:flex; gap:12px;
                       align-items:center; }
</style>
<section class="panel" id="settings">
  <h2>Frame settings</h2>
  <div class="fields">
    <div class="field">
      <label for="s_label">Place label</label>
      <span class="tip">Printed on the posters. A neighbourhood, not your
        exact address — it appears on the glass.</span>
      <input id="s_label" required minlength="1" maxlength="40">
    </div>
    <div class="field">
      <label for="s_units">Units</label>
      <span class="tip">How distances and altitudes are written.</span>
      <select id="s_units">
        <option value="metric">km / metres</option>
        <option value="aviation">nautical miles / feet</option>
      </select>
    </div>
    <div class="field wide">
      <label>Home position</label>
      <span class="tip">Where the frame hangs; aircraft are collected around
        this point. Use the button (allow the location prompt) or paste
        decimal coordinates from any map app.</span>
      <div class="pair">
        <input id="s_lat" type="number" required min="-90" max="90" step="any"
               placeholder="latitude">
        <input id="s_lon" type="number" required min="-180" max="180"
               step="any" placeholder="longitude">
        <button type="button" id="s_locate">Use my location</button>
      </div>
    </div>
    <div class="field">
      <label for="s_radius">Radius (nautical miles)</label>
      <span class="tip">The posters' field of view around home. 25 suits a
        city; bigger shows distant cruisers too. 5&#8211;250.</span>
      <input id="s_radius" type="number" required min="5" max="250" step="1">
    </div>
    <div class="field">
      <label for="s_refresh">Refresh every (minutes)</label>
      <span class="tip">How often the frame wakes for a new poster. Shorter
        is fresher; longer stretches the battery. 3&#8211;360.</span>
      <input id="s_refresh" type="number" required min="3" max="360" step="1">
    </div>
    <div class="field">
      <label>Awake hours</label>
      <span class="tip">Outside this window the frame sleeps on its last
        poster. In the timezone alongside.</span>
      <div class="pair">
        <input id="s_from" type="time" required>
        <input id="s_until" type="time" required>
      </div>
    </div>
    <div class="field">
      <label for="s_tz">Timezone</label>
      <span class="tip">IANA name; governs awake hours and timestamps.</span>
      <input id="s_tz" required pattern="[A-Za-z_]+/[A-Za-z_+\\-0-9]+"
             placeholder="Europe/London" list="s_tzs">
      <datalist id="s_tzs"></datalist>
    </div>
  </div>
  <div class="actions">
    <button id="s_save" class="primary">Save</button>
    <span class="note" id="s_msg"></span>
  </div>
</section>
<script>
function s_fail(msg) {
  const el = document.getElementById("s_msg");
  el.textContent = msg; el.className = "note err";
  setTimeout(() => { el.textContent = ""; el.className = "note"; }, 4000);
}
async function loadSettings() {
  const r = await fetch("/api/settings"); if (!r.ok) return;
  const s = await r.json();
  s_label.value = s.label; s_lat.value = s.lat; s_lon.value = s.lon;
  s_radius.value = s.radius_nm; s_units.value = s.units;
  s_refresh.value = s.refresh_minutes; s_from.value = s.awake_from;
  s_until.value = s.awake_until; s_tz.value = s.tz;
  try {
    for (const z of Intl.supportedValuesOf("timeZone")) {
      const o = document.createElement("option"); o.value = z;
      document.getElementById("s_tzs").appendChild(o);
    }
  } catch (e) {}
}
document.getElementById("s_locate").addEventListener("click", () => {
  if (!navigator.geolocation) { s_fail("no location support in this browser"); return; }
  navigator.geolocation.getCurrentPosition(
    p => { s_lat.value = p.coords.latitude.toFixed(5);
           s_lon.value = p.coords.longitude.toFixed(5); },
    e => s_fail("location refused: " + e.message),
    {enableHighAccuracy: true, timeout: 10000});
});
document.getElementById("s_save").addEventListener("click", async () => {
  for (const el of document.querySelectorAll("#settings input"))
    if (!el.checkValidity()) { s_fail("check the highlighted field"); el.focus(); return; }
  if (s_from.value >= s_until.value) { s_fail("awake window must start before it ends"); return; }
  const body = {label: s_label.value.trim(), lat: parseFloat(s_lat.value),
    lon: parseFloat(s_lon.value), radius_nm: parseFloat(s_radius.value),
    units: s_units.value, refresh_minutes: parseInt(s_refresh.value, 10),
    awake_from: s_from.value, awake_until: s_until.value, tz: s_tz.value.trim()};
  const r = await fetch("/api/settings", {method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)});
  const j = await r.json();
  if (j.ok) {
    const el = document.getElementById("s_msg");
    el.textContent = "saved \u2014 takes effect from the next render and wake";
    el.className = "note"; setTimeout(() => el.textContent = "", 4000);
  } else s_fail(j.message || "failed");
});
loadSettings();

async function loadFlights() {
  const r = await fetch("/api/flights"); if (!r.ok) return;
  const j = await r.json();
  document.getElementById("fl_add").hidden = !j.own;
  document.getElementById("fl_follow").hidden = j.own;
  const rows = document.getElementById("fl_rows");
  document.getElementById("fl_table").hidden = j.flights.length === 0;
  rows.innerHTML = j.flights.map(f => `<tr>
    <td>${f.date}${f.dep_time ? " " + f.dep_time : ""}</td>
    <td>${f.flight_no}</td>
    <td>${(f.origin || "?")} \u2013 ${(f.destination || "?")}</td>
    <td>${f.aircraft || ""}</td>
    <td class="st">${f.status}</td>
    <td>${j.own ? `<button class="del" data-id="${f.id}">remove</button>` : ""}</td>
  </tr>`).join("");
  for (const b of rows.querySelectorAll(".del"))
    b.addEventListener("click", async () => {
      await fetch("/api/flights/delete", {method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({id: parseInt(b.dataset.id, 10)})});
      loadFlights();
    });
}
document.getElementById("f_save").addEventListener("click", async () => {
  const body = {flight_no: f_no.value.trim(), date: f_date.value,
    dep_time: f_time.value, aircraft: f_aircraft.value,
    origin: f_from.value, destination: f_to.value, note: f_note.value};
  const r = await fetch("/api/flights", {method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)});
  const j = await r.json();
  const el = document.getElementById("fl_msg");
  if (j.ok) { el.className = "note"; el.textContent = "added";
    f_no.value = f_date.value = f_time.value = f_aircraft.value =
      f_from.value = f_to.value = f_note.value = "";
    loadFlights();
  } else { el.className = "note err"; el.textContent = j.message || "failed"; }
  setTimeout(() => { el.className = "note"; el.textContent = ""; }, 4000);
});
loadFlights();
</script>
"""
