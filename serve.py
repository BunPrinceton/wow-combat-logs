#!/usr/bin/env python3
"""
serve.py - local web UI for the WoW log analyzer.

Parses a combat log ONCE into a JSON report, then serves an interactive page
at http://localhost:<port>/ so you can click through every encounter, switch
between Damage / Healing / Deaths / Pull, and drill into any player.

Usage:
    python serve.py <logfile> [<logfile> ...] [--me Nazna] [--port 8777]

Pass more than one log to compare runs across logs (week-over-week) in the UI.

Pure stdlib (http.server) + a static index.html. Nothing leaves your machine.
"""
import json
import os
import sys
import webbrowser
from collections import defaultdict
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wowlogs as wl  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def densify(bins, length):
    """Turn a {second -> amount} dict into a 0-filled list of `length` seconds.
    Any event past the last second (a sliver at the very end) folds into the last bin."""
    out = [0] * length
    for sec, amt in bins.items():
        if sec >= 0:
            out[min(sec, length - 1)] += amt
    return out


def build_report(path, me=None):
    """Single pass over the log -> one JSON-able dict covering every encounter."""
    encs = wl.find_encounters(path)
    pet2owner = wl.build_pet_owner(path)

    def new_acc(enc):
        return {
            "name": enc["name"], "pull": enc["pull"], "kill": enc["kill"],
            "boss": enc["name"], "start": enc["start"], "end": enc["end"],
            "dmg": {}, "heal": {}, "players": {}, "deaths": [],
            "last_hit": {}, "first_on_boss": None,
            "aggro": [], "cur_target": None,
            # per-second raid throughput bins {second -> raw amount} (for time charts)
            "dmg_ps": defaultdict(int), "heal_ps": defaultdict(int),
        }

    accs = [new_acc(e) for e in encs]

    def player_slot(a, pname):
        return a["players"].setdefault(
            pname, {"damage": 0, "heal": 0, "spells": {}, "healspells": {},
                    "dmg_ps": defaultdict(int), "heal_ps": defaultdict(int)})

    idx = 0
    for ev in wl.iter_events(path):
        ts = ev.ts
        if ts is None:
            continue
        while idx < len(encs) and encs[idx]["end"] < ts:
            idx += 1
        if idx >= len(encs):
            break
        enc = encs[idx]
        if ts < enc["start"]:
            continue
        a = accs[idx]
        name = ev.name

        if name in wl.DAMAGE_EVENTS:
            d = wl.dmg_fields(ev)
            if not d:
                continue
            guid, sname, amt, crit, spell = d
            dst_guid, dst_name = ev.params[4], ev.params[5]
            # death attribution: remember last hit on each player
            if dst_guid.startswith("Player-"):
                a["last_hit"][dst_guid] = (wl.short(ev.params[1]), spell, amt)
            # damage rollup (pets -> owner)
            owner_player = None
            if guid.startswith("Player-"):
                key, disp, petamt, owner_player = guid, sname, 0, wl.short(sname)
                splabel = spell
            elif guid in pet2owner:
                og, on = pet2owner[guid]
                key, disp, petamt, owner_player = og, on, amt, wl.short(on)
                splabel = spell + " (pet)"
            elif guid.startswith("Pet-"):
                key, disp, petamt = "PET:" + guid, "(pet) " + wl.short(sname), amt
                splabel = None
            else:
                key = None
            sec = int((ts - enc["start"]).total_seconds())
            if key is not None:
                slot = a["dmg"].setdefault(key, [wl.short(disp), 0, 0])
                slot[1] += amt
                slot[2] += petamt
                a["dmg_ps"][sec] += amt  # raid throughput (incl. pets), per second
            if owner_player is not None:
                ps = player_slot(a, owner_player)
                ps["damage"] += amt
                ps["dmg_ps"][sec] += amt  # this player's throughput (incl. their pets)
                sp = ps["spells"].setdefault(
                    splabel, {"dmg": 0, "hits": 0, "crits": 0, "max": 0})
                sp["dmg"] += amt
                sp["hits"] += 1
                sp["crits"] += int(crit)
                sp["max"] = max(sp["max"], amt)
            # pull / aggro inference
            if a["first_on_boss"] is None and dst_name == a["boss"] and guid.startswith("Player-"):
                a["first_on_boss"] = {
                    "who": wl.short(sname), "spell": spell,
                    "t": round((ts - enc["start"]).total_seconds(), 1)}
            if name.startswith("SWING") and sname == a["boss"] and dst_name.strip():
                tgt = wl.short(dst_name)
                if tgt != a["cur_target"]:
                    a["cur_target"] = tgt
                    a["aggro"].append(
                        {"t": round((ts - enc["start"]).total_seconds(), 1), "target": tgt})

        elif name in wl.HEAL_EVENTS:
            h = wl.heal_fields(ev)
            if not h:
                continue
            guid, hname, eff, over, crit, spell = h
            if not guid.startswith("Player-"):
                continue
            slot = a["heal"].setdefault(guid, [wl.short(hname), 0, 0])
            slot[1] += eff
            slot[2] += over
            sec = int((ts - enc["start"]).total_seconds())
            a["heal_ps"][sec] += eff  # raid effective HPS, per second
            ps = player_slot(a, wl.short(hname))
            ps["heal"] += eff
            ps["heal_ps"][sec] += eff  # this healer's throughput, per second
            hs = ps["healspells"].setdefault(spell, {"eff": 0, "over": 0, "hits": 0})
            hs["eff"] += eff
            hs["over"] += over
            hs["hits"] += 1

        elif name == "UNIT_DIED":
            dguid, dname = ev.params[4], ev.params[5]
            if not dguid.startswith("Player-"):
                continue
            src, spell, amt = a["last_hit"].get(dguid, ("?", "?", 0))
            a["deaths"].append({
                "t": round((ts - enc["start"]).total_seconds(), 1),
                "who": wl.short(dname), "by": src, "spell": spell, "amount": amt})

    # serialize
    out_encs = []
    for e, a in zip(encs, accs):
        dur = (e["end"] - e["start"]).total_seconds() or 1
        dmg_rows = sorted(a["dmg"].values(), key=lambda r: r[1], reverse=True)
        heal_rows = sorted(a["heal"].values(), key=lambda r: r[1], reverse=True)
        nbins = int(dur)
        players = {}
        for pname, ps in a["players"].items():
            players[pname] = {
                "damage": ps["damage"], "dps": round(ps["damage"] / dur, 1),
                "heal": ps["heal"], "hps": round(ps["heal"] / dur, 1),
                "spells": [
                    {"spell": s, "dmg": v["dmg"], "hits": v["hits"],
                     "crits": v["crits"], "max": v["max"]}
                    for s, v in sorted(ps["spells"].items(),
                                       key=lambda kv: kv[1]["dmg"], reverse=True)],
                "healspells": [
                    {"spell": s, "eff": v["eff"], "over": v["over"], "hits": v["hits"]}
                    for s, v in sorted(ps["healspells"].items(),
                                       key=lambda kv: kv[1]["eff"], reverse=True)],
            }
            # per-second raw throughput for the time charts; omit when empty to trim payload
            if ps["dmg_ps"]:
                players[pname]["dmgSeries"] = densify(ps["dmg_ps"], nbins)
            if ps["heal_ps"]:
                players[pname]["healSeries"] = densify(ps["heal_ps"], nbins)
        out_encs.append({
            "log": os.path.basename(path),
            "name": e["name"], "pull": e["pull"], "kill": e["kill"],
            "duration": int(dur), "boss": e["name"],
            "dps": [{"player": r[0], "total": r[1], "dps": round(r[1] / dur, 1),
                     "petPct": round(100 * r[2] / r[1]) if r[1] and r[2] else 0}
                    for r in dmg_rows],
            "hps": [{"player": r[0], "eff": r[1], "hps": round(r[1] / dur, 1),
                     "overPct": round(100 * r[2] / (r[1] + r[2])) if (r[1] + r[2]) else 0}
                    for r in heal_rows],
            "deaths": a["deaths"],
            "first_on_boss": a["first_on_boss"],
            "aggro": a["aggro"],
            "series": {"dmg": densify(a["dmg_ps"], nbins),
                       "heal": densify(a["heal_ps"], nbins)},
            "players": players,
        })
    return {"log": os.path.basename(path), "me": me, "encounters": out_encs}


def make_handler(report_bytes):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # quiet

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/report"):
                self._send(report_bytes, "application/json")
            elif self.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(f.read(), "text/html; charset=utf-8")
            else:
                self.send_error(404)
    return Handler


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    me, port = None, 8777
    paths = []
    rest = argv[1:]
    i = 0
    while i < len(rest):
        if rest[i] == "--me":
            me = rest[i + 1]; i += 2
        elif rest[i] == "--port":
            port = int(rest[i + 1]); i += 2
        else:
            paths.append(rest[i]); i += 1
    if not paths:
        print(__doc__)
        return 1

    # Parse each log (one pass apiece) and merge their encounters into a single
    # pool. Each encounter is tagged with its source log (in build_report) and a
    # stable id so the UI can address any run from any log.
    encounters = []
    for path in paths:
        print(f"Parsing {os.path.basename(path)} ... (large logs take a few seconds)")
        encounters.extend(build_report(path, me)["encounters"])
    for i, e in enumerate(encounters):
        e["id"] = i
    report = {
        "log": ", ".join(os.path.basename(p) for p in paths),
        "logs": [os.path.basename(p) for p in paths],
        "me": me,
        "encounters": encounters,
    }
    n = len(encounters)
    report_bytes = json.dumps(report).encode("utf-8")
    print(f"Parsed {n} encounters from {len(paths)} log(s). "
          f"Serving UI at http://localhost:{port}/")
    print("Press Ctrl+C to stop.")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(report_bytes))
    try:
        webbrowser.open(f"http://localhost:{port}/")
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
