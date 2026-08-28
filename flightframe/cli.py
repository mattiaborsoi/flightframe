"""Command line: collect data, render designs, inspect coverage.

    python -m flightframe.cli render liveried
    python -m flightframe.cli render all --dither
    python -m flightframe.cli collect --loop 60
    python -m flightframe.cli serve
    python -m flightframe.cli coverage
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from . import canvas as canvas_mod
from .display import Selection
from .registry import Registry
from .tracking import Tracker
from . import config, palette, shapes as shapes_mod, sources
from .render import BY_NAME, NAMES as DESIGN_NAMES
from .render import flight as flight_design
from .render import liveried, portrait, rose, section
from .web import serve
from .store import Store

DESIGNS = DESIGN_NAMES


def _context(settings, *, enrich_limit: int | None = None, live: bool = True):
    # Enrich the whole snapshot by default. Labelling only the nearest dozen
    # left most of the cross-section showing callsigns, which is precisely the
    # information the redesign was meant to remove. adsbdb results are cached
    # on disk forever, so this is expensive exactly once.
    lib = shapes_mod.Library(settings.cache_dir, settings.user_agent)
    aircraft: list[sources.Aircraft] | None = []
    if live:
        # Prefer the collector's snapshot: one adsb.lol poll serves every
        # consumer. Fall back to a direct fetch so single-process dev use
        # (render without a collector running) keeps working.
        key = sources.lockey(settings.lat, settings.lon, settings.radius_nm)
        aircraft = sources.read_snapshot(settings.cache_dir, key,
                                         settings.min_altitude_ft)
        if aircraft is None:
            aircraft = sources.fetch(settings.lat, settings.lon,
                                     settings.radius_nm, settings.user_agent,
                                     settings.min_altitude_ft)
        if aircraft:
            sources.Enricher(settings.cache_dir, settings.user_agent).apply(
                aircraft, limit=enrich_limit)
    return lib, aircraft


def cmd_render(args, settings) -> int:
    if not args.loop:
        return _render_once(args, settings)
    while True:                     # keeps a warm image on disk at all times
        try:
            _render_once(args, settings)
        except Exception as exc:    # a bad render must not kill the service
            print(f"render failed: {exc!r}", file=sys.stderr, flush=True)
        try:
            time.sleep(args.loop)
        except KeyboardInterrupt:
            return 0


def _render_once(args, settings) -> int:
    designs = DESIGNS if args.design == "all" else (args.design,)
    needs_live = bool(designs)
    lib, aircraft = _context(settings, live=needs_live)

    if needs_live and aircraft is None:
        # The fetch failed rather than finding an empty sky. Leave the existing
        # posters alone: a stale render is far better than one that confidently
        # reports nothing overhead.
        print("adsb.lol unreachable — keeping the previous renders",
              file=sys.stderr, flush=True)
        designs = tuple(d for d in designs if BY_NAME[d].needs_history)
        if not designs:
            return 1
        aircraft = []

    now = datetime.now()
    tracker = Tracker(settings.data_dir, settings.cache_dir, settings.user_agent)
    for design in designs:
        c = None
        if design == "flight":
            tracked = tracker.poll()
            if tracked is None:
                # Remove the stale poster. Otherwise the last tracked flight
                # lingers in the gallery for as long as the files sit there —
                # a six-day-old "LH404, 6h to go" card long after it landed.
                for suffix in (".png", ".bin", ".svg"):
                    (settings.out_dir / f"flight{suffix}").unlink(missing_ok=True)
                continue
            c = flight_design.render(tracked, label=settings.label, shapes=lib,
                                     units=settings.units, now=now,
                                     footnote=getattr(args, "flight_footnote",
                                                      None),
                                     schedule_line=getattr(
                                         args, "flight_schedule", None),
                                     estimated=getattr(
                                         args, "flight_estimated", None),
                                     lang=getattr(args, "flight_lang",
                                                  "en"))
        elif design == "liveried":
            c = liveried.render(
                aircraft, label=settings.label, lat=settings.lat, lon=settings.lon,
                shapes=lib, units=settings.units, background=args.background,
                edition=args.edition, show_coords=args.coords, now=now)
        elif design == "section":
            c = section.render(aircraft, label=settings.label,
                               radius_km=args.section_km or settings.section_radius_km,
                               units=settings.units,
                               floor_ft=settings.min_altitude_ft, now=now)
        elif design == "portrait":
            picked = portrait.choose(aircraft, args.mode)
            if not picked:
                print("portrait: nothing overhead to draw", file=sys.stderr)
                continue
            ac, heading = picked
            c = portrait.render(ac, heading, label=settings.label, shapes=lib,
                                units=settings.units, now=now)
        elif design == "rose":
            store = Store(settings.data_dir / "positions.sqlite")
            meta = store.track_meta(settings.trace_hours)
            lo, hi = store.span(settings.trace_hours)
            store.close()
            points = _rose_points(_destinations(meta, settings))
            if not points:
                print("rose: no routed traffic in the window yet", file=sys.stderr)
                continue
            c = rose.render(
                points, label=settings.label, units=settings.units,
                since=datetime.fromtimestamp(lo) if lo else None,
                until=datetime.fromtimestamp(hi) if hi else None, now=now)

        if c is None:
            continue
        written = canvas_mod.render(c, settings.out_dir, design,
                                    dither=args.dither, keep_svg=args.svg)
        print(f"{design:9} -> {written['png'].name}  "
              f"({written['bin'].stat().st_size:,} bytes packed)", flush=True)
    return 0


def _destinations(meta, settings) -> dict[str, dict]:
    """hex -> destination airport, for the rose's spokes.

    Enrichment is cached on disk forever, so this is one lookup per callsign
    ever seen rather than one per render.
    """
    enricher = sources.Enricher(settings.cache_dir, settings.user_agent)
    out: dict[str, dict] = {}
    for hexcode, (callsign, _type) in meta.items():
        if not callsign:
            continue
        route = enricher.route(callsign)
        dest = (route or {}).get("destination") or {}
        city = dest.get("municipality") or dest.get("iata_code")
        if not city:
            continue
        entry = {"city": city, "iata": dest.get("iata_code")}
        try:
            dlat, dlon = float(dest["latitude"]), float(dest["longitude"])
        except (KeyError, TypeError, ValueError):
            out[hexcode] = entry
            continue
        entry["km"] = sources.haversine_nm(settings.lat, settings.lon, dlat, dlon) * 1.852
        entry["bearing"] = sources.bearing(settings.lat, settings.lon, dlat, dlon)
        out[hexcode] = entry
    enricher.save()
    return out


def _rose_points(destinations: dict[str, dict]) -> list[dict]:
    """One entry per distinct destination, nearest duplicate wins."""
    by_city: dict[str, dict] = {}
    for entry in destinations.values():
        if "km" not in entry or entry["km"] < 60:      # same-city hops, and home
            continue
        by_city.setdefault(entry["city"], entry)
    return list(by_city.values())


def cmd_collect(args, settings) -> int:
    store = Store(settings.data_dir / "positions.sqlite")
    try:
        while True:
            found = sources.fetch(settings.lat, settings.lon, settings.radius_nm,
                                  settings.user_agent, settings.min_altitude_ft)
            if found is None:
                print(f"{datetime.now():%H:%M:%S}  adsb.lol unreachable, skipping",
                      flush=True)
                if not args.loop:
                    return 1
                time.sleep(args.loop)
                continue
            n = store.record(found)
            print(f"{datetime.now():%H:%M:%S}  {n:3} aircraft  "
                  f"({store.count(settings.trace_hours)} distinct in "
                  f"{settings.trace_hours}h)", flush=True)
            if args.prune:
                store.prune(args.prune)
            if not args.loop:
                return 0
            time.sleep(args.loop)
    except KeyboardInterrupt:
        return 0
    finally:
        store.close()


def cmd_track(args, settings) -> int:
    tracker = Tracker(settings.data_dir, settings.cache_dir, settings.user_agent)
    if args.flight.lower() in ("off", "none", "stop", "clear"):
        tracker.clear()
        print("tracking cleared")
        return 0
    flight, message = tracker.start(args.flight)
    print(message)
    if flight is None:
        return 1
    tracker.poll()
    return 0


def cmd_display(args, settings) -> int:
    sel = Selection(settings.data_dir)
    if not args.design:
        tracked = Tracker(settings.data_dir, settings.cache_dir,
                          settings.user_agent).load() is not None
        print(f"chosen: {sel.current()}")
        print(f"showing: {sel.effective(tracked)}")
        print(f"options: {', '.join(sel.selectable())}")
        return 0
    ok, message = sel.set(args.design)
    print(message)
    return 0 if ok else 1


def cmd_coverage(args, settings) -> int:
    """How much of what is actually overhead has a shape available."""
    lib, aircraft = _context(settings, enrich_limit=0)
    if not aircraft:
        print("nothing overhead", file=sys.stderr)
        return 1
    have = miss = 0
    missing: dict[str, int] = {}
    for ac in aircraft:
        if lib.get(ac.type) and lib.resolve(ac.type) != shapes_mod.FALLBACK:
            have += 1
        else:
            miss += 1
            missing[ac.type or "?"] = missing.get(ac.type or "?", 0) + 1
    total = have + miss
    print(f"shape coverage: {have}/{total} aircraft ({100 * have / total:.0f}%)")
    if missing:
        print("no shape for:", ", ".join(f"{k}×{v}" for k, v in
                                         sorted(missing.items(), key=lambda kv: -kv[1])))
    return 0


def cmd_serve(args, settings=None) -> int:
    app = config.load_app()
    serve(app, Registry(app.registry_path), args.host, args.port)
    return 0


# -- multi-tenant service loops -------------------------------------------

def cmd_run_collector(args) -> int:
    """One process polls adsb.lol for every tenant: one fetch per distinct
    location group per cycle (jittered), snapshot for the renderers, rows
    into each tenant's own history DB."""
    import random
    app = config.load_app()
    registry = Registry(app.registry_path)
    while True:
        started = time.time()
        groups: dict[str, list[dict]] = {}
        for tenant in registry.tenants():
            key = sources.lockey(tenant["lat"], tenant["lon"],
                                 tenant["radius_nm"])
            groups.setdefault(key, []).append(tenant)
        for key, members in groups.items():
            first = members[0]
            found = sources.fetch(first["lat"], first["lon"],
                                  first["radius_nm"], app.user_agent,
                                  min_altitude_ft=0)
            if found is None:
                print(f"{datetime.now():%H:%M:%S}  {key}: adsb.lol "
                      "unreachable, skipping", flush=True)
                continue
            sources.write_snapshot(app.cache_dir, key, found)
            for tenant in members:
                floor = float(tenant["min_altitude_ft"])
                rows = [a for a in found
                        if a.altitude_ft is None or a.altitude_ft >= floor]
                store = Store(app.tenant_data(tenant["id"]) / "positions.sqlite")
                store.record(rows)
                store.prune(args.prune)
                store.close()
            print(f"{datetime.now():%H:%M:%S}  {key}: {len(found):3} aircraft "
                  f"-> {len(members)} tenant(s)", flush=True)
            time.sleep(random.uniform(0.5, 2.0))     # jitter between groups
        if not args.loop:
            return 0
        try:
            time.sleep(max(1.0, args.loop - (time.time() - started)))
        except KeyboardInterrupt:
            return 0


def cmd_run_renderer(args) -> int:
    """Render every active tenant's posters from the collector snapshots."""
    from .render import charge as charge_design
    app = config.load_app()
    registry = Registry(app.registry_path)
    while True:
        started = time.time()
        for tenant in registry.tenants():
            settings = config.for_tenant(app, tenant)
            fake = argparse.Namespace(
                design="all", dither=False, svg=False, background="blue",
                edition=1, coords=False, mode="furthest",
                section_km=None, loop=0)
            try:
                from . import schedule as schedule_mod
                schedule_mod.refresh_due(registry, tenant["id"],
                                         app.schedule_api_key,
                                         settings.cache_dir,
                                         settings.user_agent,
                                         provider=app.schedule_provider)
                _activate_due_flights(registry, tenant, settings)
                fake.flight_lang = tenant.get("lang") or "en"
                fake.flight_footnote = _next_flight_line(registry, tenant,
                                                          settings)
                fake.flight_schedule = _tracked_schedule_line(
                    registry, tenant, settings)
                fake.flight_estimated = None
                tr = Tracker(settings.data_dir, settings.cache_dir,
                             settings.user_agent).load()
                if tr is not None:
                    for row in registry.flights_for(tenant["id"],
                                                    resolve_follow=True):
                        if row["flight_no"] != tr.query:
                            continue
                        if _sync_hints(tr, row):
                            Tracker(settings.data_dir, settings.cache_dir,
                                    settings.user_agent).save(tr)
                        if tr.last_seen is None:
                            fake.flight_estimated = _estimated_progress(row)
                        break
                _render_once(fake, settings)
                # The charge poster: cheap, and canvas.render skips the write
                # (and therefore the frame skips the blit) when unchanged.
                canvas_mod.render(
                    charge_design.render(label=settings.label,
                                         lang=tenant.get("lang") or "en"),
                    settings.out_dir, "charge")
                _render_next(registry, tenant, settings)
            except Exception as exc:     # one tenant must not kill the rest
                print(f"render[{tenant['id']}] failed: {exc!r}",
                      file=sys.stderr, flush=True)
        if not args.loop:
            return 0
        try:
            time.sleep(max(1.0, args.loop - (time.time() - started)))
        except KeyboardInterrupt:
            return 0


# -- admin ------------------------------------------------------------------

def _activate_due_flights(registry, tenant, settings) -> None:
    """Start live tracking for a listed flight on its day.

    Runs in the renderer's per-tenant pass. Manual tracking always wins: if
    anything is already tracked (or holding after landing), listed flights
    wait. A flight still untracked by the end of its day is marked missed —
    wrong number, cancelled, or never picked up by the receivers.
    """
    from datetime import date as _date
    from zoneinfo import ZoneInfo
    try:
        today = datetime.now(ZoneInfo(tenant["tz"])).date()
    except Exception:
        today = _date.today()
    # A family frame follows the traveller's list and may track the flight on
    # its own glass, but the row STATUS is the traveller's bookkeeping alone —
    # a follower must never write it, or the two renderer passes race over the
    # same rows and mark flights done before they depart.
    is_owner = not tenant.get("follows_flights_of")
    flights = registry.flights_for(tenant["id"], resolve_follow=True)
    tracker = Tracker(settings.data_dir, settings.cache_dir,
                      settings.user_agent)
    active = tracker.load()
    free = active is None or active.status == "expired"   # manual tracking wins
    try:
        now_local = datetime.now(ZoneInfo(tenant["tz"]))
    except Exception:
        now_local = datetime.now()
    for row in flights:
        due = _date.fromisoformat(row["date"])
        cancelled = str(row.get("airline_status") or "").startswith("Cancel")
        if row["status"] == "tracking" and _blind_flight_over(row):
            current = tracker.load()
            if (current is not None and current.query == row["flight_no"]
                    and current.last_seen is None):
                # Only the blind case: a flight the receivers actually saw
                # ends through the tracker's own landed/hold lifecycle.
                tracker.clear()
                free = True
            if is_owner:
                registry.flight_set_status(
                    row["id"], "missed" if cancelled else "done")
            continue
        if (due == today and free and not cancelled
                and _takeover_open(row, now_local, settings)):
            flight, _msg = tracker.start(row["flight_no"])
            if flight is not None:
                # The schedule API outranks the static route database on
                # where this dated flight is actually going (BA588's route
                # entry says Linate; today's schedule says Malpensa). Keep
                # the database's coordinates, show the schedule's airports.
                changed = False
                for side in ("origin", "destination"):
                    code = (row.get(side) or "").upper()
                    ap = getattr(flight, side)
                    if code and len(code) == 3 and ap.get("iata") != code:
                        ap["iata"] = code
                        if row.get(f"{side}_city"):
                            ap["city"] = row[f"{side}_city"]
                        changed = True
                changed |= _sync_hints(flight, row)
                if changed:
                    tracker.save(flight)
                tracker.poll()
                free = False                # this frame's glass is now taken
                if is_owner:
                    registry.flight_set_status(row["id"], "tracking")
                print(f"flight[{tenant['id']}]: tracking {row['flight_no']}",
                      flush=True)
        if not is_owner:
            continue
        if row["status"] == "tracking" and free:
            registry.flight_set_status(
                row["id"], "done" if due <= today else "upcoming")
        elif due < today and row["status"] != "tracking":
            registry.flight_set_status(row["id"], "missed")


def _tracked_schedule_line(registry, tenant, settings) -> str | None:
    """The schedule line for the flight on this frame's tracker. Matched by
    flight number, not row status: the status flip belongs to the OWNER's
    render pass, and a follower frame renders first when its tenant id
    sorts first — keying on status left the famiglia frame's poster
    without times for a pass."""
    tracked = Tracker(settings.data_dir, settings.cache_dir,
                      settings.user_agent).load()
    if tracked is None:
        return None
    for row in registry.flights_for(tenant["id"], resolve_follow=True):
        if row["flight_no"] == tracked.query and row["status"] != "done":
            return _schedule_line(row, tenant)
    return None


def _schedule_line(row, tenant) -> str | None:
    """"TPA 12:31 – PHL 15:16 · in Italia 18:31 – 21:16": the airports'
    own clocks first, the viewer's second. Needs the stored per-airport
    UTC offsets; without them only the airport clocks are shown."""
    from datetime import datetime as _dt, timedelta, timezone as _tz
    from zoneinfo import ZoneInfo
    if not (row.get("dep_time") and row.get("arr_time")):
        return None
    left = (f"{(row.get('origin') or '').upper()} {row['dep_time']} – "
            f"{(row.get('destination') or '').upper()} {row['arr_time']}")
    if row.get("dep_offset_min") is None or row.get("arr_offset_min") is None:
        return left
    try:
        tz = ZoneInfo(tenant["tz"])
        day = _dt.fromisoformat(row["date"])
        def local(hhmm, off, plus_days=0):
            t = day.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:5]),
                            tzinfo=_tz(timedelta(minutes=off)))
            return (t + timedelta(days=plus_days)).astimezone(tz)
        dep = local(row["dep_time"], row["dep_offset_min"])
        arr = local(row["arr_time"], row["arr_offset_min"],
                    row.get("arr_day_offset") or 0)
        where = ("in Italia" if (tenant.get("lang") or "en") == "it"
                 else tz.key.rsplit("/", 1)[-1].replace("_", " "))
        return (f"{left}   ·   {where} {dep:%H:%M} – {arr:%H:%M}"
                + (" +1" if arr.date() > dep.date() else ""))
    except Exception:
        return left


def _next_flight_line(registry, tenant, settings) -> tuple | None:
    """"Next: Milan -> Copenhagen · SK1516 · 19/Nov/2026" for the tracked
    poster's footer — the row after the one being flown, connections first."""
    from .render.next import _date_str, _row_route
    from datetime import date as _date
    tracked = Tracker(settings.data_dir, settings.cache_dir,
                      settings.user_agent).load()
    rows = [r for r in registry.flights_for(tenant["id"], resolve_follow=True)
            if r["status"] == "upcoming"
            and (tracked is None or r["flight_no"] != tracked.query)]
    if not rows:
        return None
    row = rows[0]
    lang = tenant.get("lang") or "en"
    word = "Poi" if lang == "it" else "Next"
    d = _date.fromisoformat(row["date"])
    detail = [row["flight_no"], _date_str(d, lang)]
    if row.get("dep_time"):
        detail.append(row["dep_time"])
    return (word, _row_route(row), " · ".join(detail))


def _sync_hints(flight, row) -> bool:
    """Keep the tracker's identification hints fresh from the schedule:
    tail registration, expected airframe type, and the departure instant
    that opens the origin-airport hunt window."""
    from .render.next import _type_code
    changed = False
    if row.get("registration") and flight.registration != row["registration"]:
        flight.registration = row["registration"]
        changed = True
    hint = _type_code(row.get("aircraft"))
    if hint and flight.type_hint != hint:
        flight.type_hint = hint
        changed = True
    dep = _dep_instant(row)
    epoch = dep.timestamp() if dep else None
    if epoch and flight.dep_epoch != epoch:
        flight.dep_epoch = epoch
        changed = True
    return changed


def _arr_instant(row):
    """Absolute arrival time as an aware datetime, or None."""
    from datetime import datetime, timedelta, timezone
    if not (row.get("arr_time") and row.get("arr_offset_min") is not None):
        return None
    try:
        t = datetime.fromisoformat(f"{row['date']} {row['arr_time']}")
        t = t.replace(tzinfo=timezone(timedelta(
            minutes=row["arr_offset_min"])))
        return t + timedelta(days=row.get("arr_day_offset") or 0)
    except ValueError:
        return None


def _dep_instant(row):
    from datetime import datetime, timedelta, timezone
    if not (row.get("dep_time") and row.get("dep_offset_min") is not None):
        return None
    try:
        t = datetime.fromisoformat(f"{row['date']} {row['dep_time']}")
        return t.replace(tzinfo=timezone(timedelta(
            minutes=row["dep_offset_min"])))
    except ValueError:
        return None


def _blind_flight_over(row) -> bool:
    """A flight tracked only through the airline feed is over when the
    airline says it landed (plus the same 30-minute hold live tracking
    gets), or long after scheduled arrival as a backstop."""
    from datetime import datetime, timedelta, timezone
    status = str(row.get("airline_status") or "")
    arr = _arr_instant(row)
    now = datetime.now(timezone.utc)
    if status.startswith("Cancel"):
        return True
    if arr is None:
        return False
    if status in ("Landed", "Arrived") and now > arr + timedelta(minutes=30):
        return True
    return now > arr + timedelta(hours=4)


def _estimated_progress(row) -> dict | None:
    """Clock-derived progress for a flight the receivers cannot see:
    the airline says it is flying, so show the fraction of the scheduled
    block time that has elapsed rather than a stuck 'waiting'."""
    from datetime import datetime, timezone
    if str(row.get("airline_status") or "") not in ("EnRoute", "Departed"):
        return None
    dep, arr = _dep_instant(row), _arr_instant(row)
    if not (dep and arr) or arr <= dep:
        return None
    now = datetime.now(timezone.utc)
    frac = (now - dep).total_seconds() / (arr - dep).total_seconds()
    return {"frac": min(max(frac, 0.03), 0.97)}


def _takeover_open(row, now_local, settings) -> bool:
    """Should the tracked-flight design take the glass yet?

    The board used to switch over at midnight and show "waiting for it to
    take off" for an entire day. The window opens 2 hours before the
    scheduled departure and the tracker itself closes it 30 minutes after
    landing. Departure times are the AIRPORT's clock; when the schedule
    refresh has stored that airport's UTC offset the comparison is exact
    (a 12:31 Tampa departure is 18:31 on an Italian frame — the board
    once took over six hours early by ignoring this, and per-date offsets
    make summer time arrive already applied). Without an offset the
    airport clock is read as tenant-local, the least-wrong fallback; with
    only an arrival time, departure is estimated from the route length;
    with no times at all the whole-day behaviour stands (better a dull
    wait than a missed flight).
    """
    from datetime import timedelta, timezone as _tz
    def _at(hhmm: str, offset_min=None):
        naive = now_local.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:5]),
                                  second=0, microsecond=0)
        if offset_min is None:
            return naive
        # airport wall clock -> absolute instant -> tenant wall clock
        instant = naive.replace(tzinfo=_tz(timedelta(minutes=offset_min)))
        return instant.astimezone(now_local.tzinfo)
    try:
        if row.get("dep_time"):
            dep = _at(row["dep_time"], row.get("dep_offset_min"))
            return now_local >= dep - timedelta(hours=2)
        if row.get("arr_time"):
            hours = 1.5
            try:
                enr = sources.Enricher(settings.cache_dir, settings.user_agent)
                route = enr.route(row["flight_no"]) or {}
                o = route.get("origin") or {}
                d = route.get("destination") or {}
                km = sources.haversine_nm(o["latitude"], o["longitude"],
                                          d["latitude"],
                                          d["longitude"]) * 1.852
                hours = km / 800.0 + 0.4
            except Exception:
                pass
            start = _at(row["arr_time"], row.get("arr_offset_min")) \
                - timedelta(hours=hours, minutes=60)
            if row.get("arr_day_offset"):
                start -= timedelta(days=row["arr_day_offset"])
            return now_local >= start
    except (ValueError, TypeError):
        pass
    return True


def _render_next(registry, tenant, settings) -> None:
    """The travel board, rendered from the (possibly followed) flight list."""
    from .render import next as next_design
    flights = registry.flights_for(tenant["id"], resolve_follow=True)
    _attach_cities(flights, settings)
    followed = tenant.get("follows_flights_of")
    owner = registry.tenant(followed) if followed else tenant
    live = None
    if flights:
        from datetime import date as _date
        soon = (_date.fromisoformat(flights[0]["date"])
                - _date.today()).days <= 1
        if soon:
            live = _live_details(flights[0], settings)
    lib = shapes_mod.Library(settings.cache_dir, settings.user_agent)
    c = next_design.render(flights, name=(owner or tenant)["name"],
                           lang=tenant.get("lang") or "en", live=live,
                           shapes=lib)
    canvas_mod.render(c, settings.out_dir, "next")


def _attach_cities(flights: list[dict], settings) -> None:
    """Resolve airport codes to city names ("VCE" -> "Venice") from the
    route cache, so the board can print "Venice–VCE" instead of a bare
    code. The lookup is keyed by flight number and matched back by IATA:
    a refreshed row whose route no longer agrees with the cache simply
    keeps the bare code rather than borrowing the wrong city."""
    try:
        enr = sources.Enricher(settings.cache_dir, settings.user_agent)
        for row in flights:
            route = enr.route(row["flight_no"]) or {}
            airline = route.get("airline") or {}
            if airline.get("icao"):
                row["airline_icao"] = airline["icao"]
                row["airline_name"] = airline.get("name")
            for side in ("origin", "destination"):
                if row.get(f"{side}_city"):
                    continue          # the schedule API's own city wins
                ap = route.get(side) or {}
                code = (row.get(side) or "").upper()
                if code and ap.get("iata_code") == code and ap.get("municipality"):
                    row[f"{side}_city"] = ap["municipality"]
        enr.save()
    except Exception:
        pass                      # city names are a bonus, never a failure


def _live_details(row, settings) -> dict | None:
    """Within 24h of departure the transponder world starts to know the
    flight: pull tail number and exact type once the callsign is live."""
    try:
        # adsb.lol matches the broadcast ICAO callsign (BAW117), not the IATA
        # flight number as printed (BA117); resolve it via adsbdb first, the
        # same conversion tracking.py does. Fall back to the number as typed.
        enr = sources.Enricher(settings.cache_dir, settings.user_agent)
        route = enr.route(row["flight_no"]) or {}
        enr.save()
        callsign = route.get("callsign_icao") or row["flight_no"]
        found = sources._get(
            f"https://api.adsb.lol/v2/callsign/{callsign}",
            settings.user_agent, attempts=1)
        ac = ((found or {}).get("ac") or [None])[0]
        if not ac:
            return None
        live = {"registration": (ac.get("r") or "").strip() or None}
        enricher = sources.Enricher(settings.cache_dir, settings.user_agent)
        frame = enricher.airframe(ac.get("hex", "").strip()) or {}
        enricher.save()
        live["type_name"] = frame.get("type") or ac.get("t")
        year = frame.get("year_built") or frame.get("manufactured")
        if year:
            live["age_years"] = max(0, datetime.now().year - int(year))
        return live
    except Exception:
        return None       # enrichment is a bonus, never a failure


def cmd_tenant(args) -> int:
    app = config.load_app()
    registry = Registry(app.registry_path)
    if args.action != "list" and not args.id:
        print("tenant id required", file=sys.stderr)
        return 2
    if args.action == "add":
        extra = {"lang": args.lang} if args.lang else {}
        row = registry.tenant_add(args.id, args.name or args.id,
                                  args.lat, args.lon, args.label, **extra)
        if args.follows:
            registry.tenant_admin_update(args.id,
                                         {"follows_flights_of": args.follows})
        print(f"tenant {row['id']} created"
              + (f" (follows {args.follows})" if args.follows else ""))
        print(f"provisioning secret: {row['provision_secret']}")
    elif args.action == "list":
        for t in registry.tenants(status=None):
            print(f"{t['id']:12} {t['status']:9} {t['label']:24} "
                  f"{t['name']}")
    elif args.action == "disable":
        registry.tenant_set_status(args.id, "disabled")
        print(f"tenant {args.id} disabled (frames get 503, humans locked out)")
    elif args.action == "enable":
        registry.tenant_set_status(args.id, "active")
        print(f"tenant {args.id} enabled")
    elif args.action == "rotate-secret":
        print(f"new secret: {registry.tenant_rotate_secret(args.id)}")
    return 0


def cmd_user(args) -> int:
    import getpass
    from . import auth as auth_mod
    app = config.load_app()
    registry = Registry(app.registry_path)
    password = getpass.getpass(f"password for {args.email}: ")
    registry.user_add(args.tenant, args.email,
                      auth_mod.hash_password(password), args.admin)
    print(f"user {args.email} -> tenant {args.tenant}"
          f"{' (admin)' if args.admin else ''}")
    return 0


def cmd_migrate_legacy(args) -> int:
    """Fold a single-tenant deployment into tenant #1 of the registry.

    Reads the legacy .env for the location, moves the three state files, and
    preserves the existing device token so the frame keeps polling before it
    is re-provisioned against the cloud."""
    import hashlib as _hl
    import json as _json
    import shutil
    legacy = config.load()
    app = config.load_app()
    registry = Registry(app.registry_path)
    if registry.tenant(args.id):
        print(f"tenant {args.id} already exists — refusing", file=sys.stderr)
        return 1
    row = registry.tenant_add(
        args.id, args.name, legacy.lat, legacy.lon, legacy.label,
        radius_nm=legacy.radius_nm, units=legacy.units_name,
        refresh_minutes=legacy.refresh_minutes,
        awake_from=f"{legacy.awake_from:%H:%M}",
        awake_until=f"{legacy.awake_until:%H:%M}",
        trace_hours=legacy.trace_hours,
        min_altitude_ft=legacy.min_altitude_ft,
        section_radius_km=legacy.section_radius_km)
    dest = app.tenant_data(args.id)
    for name in ("positions.sqlite", "tracking.json", "display.json"):
        src = legacy.data_dir / name
        if src.exists() and not (dest / name).exists():
            shutil.copy2(src, dest / name)
            print(f"moved {name}")
    out_dest = app.tenant_out(args.id)
    for p in legacy.out_dir.glob("*.*"):
        if p.is_file() and not (out_dest / p.name).exists():
            shutil.copy2(p, out_dest / p.name)
    devices = legacy.data_dir / "devices.json"
    if devices.exists():
        for token, rec in _json.loads(devices.read_text()).items():
            with registry._db() as db:
                db.execute(
                    "INSERT OR IGNORE INTO devices (tenant_id, mac,"
                    " token_hash, hw_rev, first_seen, last_setup)"
                    " VALUES (?,?,?,?,?,?)",
                    (args.id, rec.get("mac", "unknown"),
                     _hl.sha256(token.encode()).hexdigest(),
                     rec.get("hw_rev"), rec.get("first_seen"),
                     rec.get("last_setup")))
            print(f"imported device {rec.get('mac')} (token preserved)")
    print(f"tenant {args.id} ready; provisioning secret: "
          f"{row['provision_secret']}")
    return 0


def cmd_where(args, settings) -> int:
    flag = "  (PUBLIC DEFAULT — set HOME_LAT/HOME_LON in .env)" \
        if settings.is_default_location else ""
    print(f"{settings.label}: {settings.lat}, {settings.lon}{flag}")
    print(f"radius {settings.radius_nm:.0f} nm · refresh {settings.refresh_minutes} min "
          f"· awake {settings.awake_from:%H:%M}–{settings.awake_until:%H:%M}")
    print(f"data {settings.data_dir}\nout  {settings.out_dir}\ncache {settings.cache_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="flightframe")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="render one design, or all of them")
    r.add_argument("design", choices=(*DESIGNS, "all"))
    r.add_argument("--dither", action="store_true",
                   help="Floyd-Steinberg instead of nearest colour")
    r.add_argument("--svg", action="store_true", help="also keep the source SVG")
    r.add_argument("--background", default="blue",
                   choices=list(palette.INKS), help="liveried: flooded background ink")
    r.add_argument("--edition", type=int, default=1)
    r.add_argument("--coords", action="store_true",
                   help="liveried: print lat/lon in the footer. Off by "
                        "default: the footer would otherwise publish the "
                        "frame's location in every photo of it")
    r.add_argument("--mode", default="furthest",
                   choices=list(portrait.SUPERLATIVES), help="portrait: how to pick")
    r.add_argument("--section-km", type=float, default=None,
                   help="section: how far out to plot")
    r.add_argument("--loop", type=int, default=0, metavar="SECONDS",
                   help="re-render every SECONDS (0 = once)")
    r.set_defaults(fn=cmd_render)

    col = sub.add_parser("collect", help="sample positions into the history database")
    col.add_argument("--loop", type=int, default=0, metavar="SECONDS",
                     help="keep sampling every SECONDS (0 = once)")
    col.add_argument("--prune", type=float, default=None, metavar="HOURS",
                     help="drop history older than HOURS after each sample")
    col.set_defaults(fn=cmd_collect)

    tr = sub.add_parser("track", help="follow one flight, or 'off' to stop")
    tr.add_argument("flight", help="flight number as printed, e.g. BA117")
    tr.set_defaults(fn=cmd_track)

    dp = sub.add_parser("display", help="which poster the frame shows")
    dp.add_argument("design", nargs="?", help="omit to see the current choice")
    dp.set_defaults(fn=cmd_display)

    cov = sub.add_parser("coverage", help="shape coverage against live traffic")
    cov.set_defaults(fn=cmd_coverage)

    sv = sub.add_parser("serve", help="multi-tenant dashboard + device API")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--host", default="0.0.0.0",
                    help="0.0.0.0 exposes it to your network; 127.0.0.1 keeps it local")
    sv.set_defaults(fn2=cmd_serve)

    wh = sub.add_parser("where", help="show the configured location and paths")
    wh.set_defaults(fn=cmd_where)

    rc = sub.add_parser("run-collector",
                        help="poll adsb.lol for every tenant location group")
    rc.add_argument("--loop", type=int, default=0, metavar="SECONDS")
    rc.add_argument("--prune", type=float, default=12, metavar="HOURS")
    rc.set_defaults(fn2=cmd_run_collector)

    rr = sub.add_parser("run-renderer",
                        help="render every active tenant's posters")
    rr.add_argument("--loop", type=int, default=0, metavar="SECONDS")
    rr.set_defaults(fn2=cmd_run_renderer)

    tn = sub.add_parser("tenant", help="manage tenants")
    tn.add_argument("action", choices=("add", "list", "disable", "enable",
                                       "rotate-secret"))
    tn.add_argument("id", nargs="?", help="tenant id (slug)")
    tn.add_argument("--name", help="display name (share pages)")
    tn.add_argument("--lat", type=float, default=51.5154)
    tn.add_argument("--lon", type=float, default=-0.1410)
    tn.add_argument("--label", default="Oxford Circus")
    tn.add_argument("--lang", default=None, choices=("en", "it"),
                    help="dashboard/poster language for this tenant")
    tn.add_argument("--follows", default=None, metavar="TENANT",
                    help="frame shows TENANT's upcoming flights (family mode)")
    tn.set_defaults(fn2=cmd_tenant)

    us = sub.add_parser("user", help="create a dashboard login")
    us.add_argument("action", choices=("add",))
    us.add_argument("tenant"), us.add_argument("email")
    us.add_argument("--admin", action="store_true")
    us.set_defaults(fn2=cmd_user)

    mg = sub.add_parser("migrate-legacy",
                        help="fold a single-tenant deployment into the registry")
    mg.add_argument("--id", default="t1")
    mg.add_argument("--name", default="Mattia")
    mg.set_defaults(fn2=cmd_migrate_legacy)

    args = p.parse_args(argv)
    if hasattr(args, "fn2"):
        return args.fn2(args)
    return args.fn(args, config.load())


if __name__ == "__main__":
    raise SystemExit(main())
