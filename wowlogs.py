#!/usr/bin/env python3
"""
wowlogs.py - an offline WoW combat-log analyzer (TBC/Classic 2.5.x advanced logs).

Commands
--------
  encounters                                  list every boss pull in the log
  dps      "<boss>" [--pull N] [--csv FILE]   damage table (pets rolled into owner)
  hps      "<boss>" [--pull N] [--csv FILE]   effective-healing table (+ overheal%)
  deaths   "<boss>" [--pull N]                death log (who died, when, killing blow)
  player   <char> "<boss>" [--pull N]         deep dive on ONE character
  pull     "<boss>" [--pull N]                who started the pull / aggro timeline
  compare  <other.log> "<boss>" [--metric dps|hps]   this log vs another (week-over-week)

Global options
  --me <char>     highlight this character (marked with >> in tables)

Examples
  python wowlogs.py log.txt encounters
  python wowlogs.py log.txt dps "Lady Vashj" --me Celions
  python wowlogs.py log.txt player Celions "Lady Vashj"
  python wowlogs.py thisweek.log compare lastweek.log "Lady Vashj" --me Celions

Notes
  * Threat is NOT in the combat log. "pull"/aggro views are *inferred* from the
    first damage on the boss and from who the boss is auto-attacking. Labeled as such.
  * Files are only ever read, never modified.
"""
import csv
import gzip
import io
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

# ---------------------------------------------------------------------------
# Field layout for the 2.5.x ADVANCED combat log.
#   After the EVENT token the params are:
#     base (8): srcGUID, srcName, srcFlags, srcRaidFlags,
#               dstGUID, dstName, dstFlags, dstRaidFlags
#     SPELL/RANGE prefix adds (3): spellId, spellName, spellSchool
#     advanced block (18): infoGUID, ownerGUID, curHP, maxHP, AP, SP, armor,
#               ..., resourceType, ..., posX, posY, mapID, facing, level
#     suffix (varies by event; offsets below are relative to the advanced end)
ADV_LEN = 18

# damage suffix:  amount, base, overkill, school, resisted, blocked,
#                 absorbed, critical(+7), glancing(+8), crushing(+9)
# heal   suffix:  amount, base, overhealing(+2), absorbed(+3), critical(+4)
DAMAGE_EVENTS = {
    "SWING_DAMAGE", "SWING_DAMAGE_LANDED",
    "SPELL_DAMAGE", "SPELL_PERIODIC_DAMAGE", "SPELL_BUILDING_DAMAGE",
    "RANGE_DAMAGE", "DAMAGE_SHIELD", "DAMAGE_SPLIT",
}
HEAL_EVENTS = {"SPELL_HEAL", "SPELL_PERIODIC_HEAL"}


def prefix_len(event_name):
    """Number of params before the advanced block."""
    if event_name.startswith("SWING"):
        return 8
    if event_name.startswith("RANGE") or event_name.startswith("SPELL") \
            or event_name.startswith("DAMAGE"):
        return 11
    return None


@dataclass
class Event:
    ts: datetime
    name: str
    params: list


def parse_line(line):
    """'M/D/YYYY H:M:S.mmm-tz  EVENT,csv...' -> Event (or None)."""
    sep = "  " if "  " in line else "\t"
    try:
        ts_part, rest = line.split(sep, 1)
    except ValueError:
        return None
    ts_part = ts_part.strip()
    base_ts = ts_part.rsplit("-", 1)[0] if "-" in ts_part[10:] else ts_part
    try:
        ts = datetime.strptime(base_ts.strip(), "%m/%d/%Y %H:%M:%S.%f")
    except ValueError:
        ts = None
    try:
        row = next(csv.reader(io.StringIO(rest)))
    except StopIteration:
        return None
    if not row:
        return None
    return Event(ts=ts, name=row[0], params=row[1:])


def _open_log(path):
    """Open a combat log for reading text. Handles gzip (.gz) transparently so
    the repo's compressed `WoWCombatLog-*.txt.gz` files work on any machine."""
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def iter_events(path, must_contain=None):
    """Stream events. If must_contain is given, cheaply skip lines without it."""
    with _open_log(path) as fh:
        for line in fh:
            if must_contain and must_contain not in line:
                continue
            line = line.rstrip("\n")
            if not line:
                continue
            ev = parse_line(line)
            if ev is not None:
                yield ev


# ---------------------------------------------------------------------------
# value extractors
def dmg_fields(ev):
    """(srcGUID, srcName, amount, is_crit, spell) for a damage event, else None."""
    plen = prefix_len(ev.name)
    if plen is None:
        return None
    ai = plen + ADV_LEN
    if ai >= len(ev.params):
        return None
    try:
        amount = int(ev.params[ai])
    except (ValueError, IndexError):
        return None
    if amount < 0:
        return None
    crit = ev.params[ai + 7] == "1" if ai + 7 < len(ev.params) else False
    if ev.name.startswith("SWING"):
        spell = "Melee"
    else:
        spell = ev.params[9] if len(ev.params) > 9 else "?"
    return ev.params[0], ev.params[1], amount, crit, spell


def heal_fields(ev):
    """(srcGUID, srcName, effective, overheal, is_crit, spell) or None."""
    plen = prefix_len(ev.name)
    if plen is None:
        return None
    ai = plen + ADV_LEN
    if ai + 4 >= len(ev.params):
        return None
    try:
        amount = int(ev.params[ai])
        overheal = int(ev.params[ai + 2])
    except (ValueError, IndexError):
        return None
    crit = ev.params[ai + 4] == "1"
    effective = amount - overheal
    spell = ev.params[9] if len(ev.params) > 9 else "?"
    return ev.params[0], ev.params[1], effective, overheal, crit, spell


def short(name):
    return name.split("-")[0] if name else name


# ---------------------------------------------------------------------------
# encounters
def find_encounters(path):
    encs, openenc, pulls = [], None, defaultdict(int)
    for ev in iter_events(path, must_contain="ENCOUNTER_"):
        if ev.name == "ENCOUNTER_START":
            openenc = {"id": ev.params[0], "name": ev.params[1], "start": ev.ts}
        elif ev.name == "ENCOUNTER_END" and openenc:
            nm = ev.params[1]
            pulls[nm] += 1
            openenc.update(end=ev.ts, kill=(ev.params[-1] == "1"), pull=pulls[nm])
            encs.append(openenc)
            openenc = None
    return encs


def pick_encounter(path, name, pull=None):
    matches = [e for e in find_encounters(path) if e["name"].lower() == name.lower()]
    if not matches:
        sys.exit(f"No encounter named {name!r}. Run the 'encounters' command to list them.")
    if pull is not None:
        matches = [e for e in matches if e["pull"] == pull]
        if not matches:
            sys.exit(f"No pull #{pull} for {name!r}.")
    return matches[-1]


def fmt_dur(start, end):
    if not start or not end:
        return "?"
    s = int((end - start).total_seconds())
    return f"{s // 60}m{s % 60:02d}s"


# ---------------------------------------------------------------------------
# pet -> owner mapping (built from SPELL_SUMMON across the whole file)
def build_pet_owner(path):
    pet2owner = {}
    for ev in iter_events(path, must_contain="SPELL_SUMMON"):
        if ev.name == "SPELL_SUMMON":
            owner_guid, owner_name = ev.params[0], ev.params[1]
            pet_guid = ev.params[4]
            pet2owner[pet_guid] = (owner_guid, owner_name)
    return pet2owner


# ---------------------------------------------------------------------------
# commands
def cmd_encounters(path, **_):
    encs = find_encounters(path)
    if not encs:
        print("No encounters found.")
        return
    print(f"{'#':>2}  {'result':6} {'pull':>4} {'duration':>8}  encounter")
    print("-" * 60)
    for i, e in enumerate(encs):
        res = "KILL" if e["kill"] else "wipe"
        print(f"{i:>2}  {res:6} {e['pull']:>4} {fmt_dur(e['start'], e['end']):>8}  {e['name']}")


def _collect_damage(path, enc, pet2owner):
    """Return {key: [display_name, dmg, pet_dmg]} keyed by owner guid."""
    start, end = enc["start"], enc["end"]
    totals = {}
    for ev in iter_events(path):
        if ev.ts is None or ev.ts < start or ev.ts > end or ev.name not in DAMAGE_EVENTS:
            continue
        d = dmg_fields(ev)
        if not d:
            continue
        guid, name, amount, _crit, _spell = d
        pet_amt = 0
        if guid.startswith("Player-"):
            key, disp = guid, name
        elif guid.startswith("Pet-") and guid in pet2owner:
            key, disp = pet2owner[guid][0], pet2owner[guid][1]
            pet_amt = amount
        elif guid.startswith("Pet-"):
            key, disp = "PET:" + guid, f"(pet) {short(name)}"
        else:
            continue
        slot = totals.setdefault(key, [disp, 0, 0])
        slot[1] += amount
        slot[2] += pet_amt
    return totals


def cmd_dps(path, name, pull=None, csv_path=None, me=None, **_):
    enc = pick_encounter(path, name, pull)
    dur = (enc["end"] - enc["start"]).total_seconds()
    totals = _collect_damage(path, enc, build_pet_owner(path))
    rows = sorted(totals.values(), key=lambda r: r[1], reverse=True)
    res = "KILL" if enc["kill"] else "wipe"
    print(f"\n{enc['name']}  (pull #{enc['pull']}, {res}, {fmt_dur(enc['start'], enc['end'])}, {dur:.0f}s)")
    print(f"   {'player':22} {'total dmg':>12} {'dps':>9} {'pet%':>5}")
    print("   " + "-" * 52)
    raid = 0
    for disp, dmg, petd in rows:
        raid += dmg
        mark = ">>" if me and short(disp).lower() == me.lower() else "  "
        petpct = f"{100*petd/dmg:.0f}%" if dmg and petd else ""
        print(f"{mark} {short(disp):22} {dmg:>12,} {dmg/dur:>9,.0f} {petpct:>5}")
    print("   " + "-" * 52)
    print(f"   {'RAID':22} {raid:>12,} {raid/dur:>9,.0f}")
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["player", "total_damage", "dps", "pet_damage"])
            for disp, dmg, petd in rows:
                w.writerow([short(disp), dmg, round(dmg/dur, 1), petd])
        print(f"\n   wrote {csv_path}")


def cmd_hps(path, name, pull=None, csv_path=None, me=None, **_):
    enc = pick_encounter(path, name, pull)
    dur = (enc["end"] - enc["start"]).total_seconds()
    start, end = enc["start"], enc["end"]
    totals = {}  # guid -> [name, effective, overheal]
    for ev in iter_events(path):
        if ev.ts is None or ev.ts < start or ev.ts > end or ev.name not in HEAL_EVENTS:
            continue
        h = heal_fields(ev)
        if not h:
            continue
        guid, nm, eff, over, _crit, _spell = h
        if not guid.startswith("Player-"):
            continue
        slot = totals.setdefault(guid, [nm, 0, 0])
        slot[1] += eff
        slot[2] += over
    rows = sorted(totals.values(), key=lambda r: r[1], reverse=True)
    res = "KILL" if enc["kill"] else "wipe"
    print(f"\n{enc['name']}  (pull #{enc['pull']}, {res}, healing)")
    print(f"   {'player':22} {'eff. heal':>12} {'hps':>9} {'overheal':>9}")
    print("   " + "-" * 56)
    for nm, eff, over in rows:
        mark = ">>" if me and short(nm).lower() == me.lower() else "  "
        opct = f"{100*over/(eff+over):.0f}%" if (eff + over) else "0%"
        print(f"{mark} {short(nm):22} {eff:>12,} {eff/dur:>9,.0f} {opct:>9}")
    if csv_path:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["player", "effective_heal", "hps", "overheal"])
            for nm, eff, over in rows:
                w.writerow([short(nm), eff, round(eff/dur, 1), over])
        print(f"\n   wrote {csv_path}")


def cmd_deaths(path, name, pull=None, me=None, **_):
    enc = pick_encounter(path, name, pull)
    start, end = enc["start"], enc["end"]
    print(f"\n{enc['name']}  (pull #{enc['pull']}) - deaths")
    print("   " + "-" * 50)
    # track last damage taken per unit to name the killing blow
    last_hit = {}  # guid -> (srcName, spell, amount)
    n = 0
    for ev in iter_events(path):
        if ev.ts is None or ev.ts < start or ev.ts > end:
            continue
        if ev.name in DAMAGE_EVENTS:
            plen = prefix_len(ev.name)
            dst = ev.params[4]
            d = dmg_fields(ev)
            if d:
                spell = "Melee" if ev.name.startswith("SWING") else (ev.params[9] if len(ev.params) > 9 else "?")
                last_hit[dst] = (short(ev.params[1]), spell, d[2])
        elif ev.name == "UNIT_DIED":
            dguid, dname = ev.params[4], ev.params[5]
            if not dguid.startswith("Player-"):
                continue
            n += 1
            t = (ev.ts - start).total_seconds()
            src, spell, amt = last_hit.get(dguid, ("?", "?", 0))
            mark = ">>" if me and short(dname).lower() == me.lower() else "  "
            print(f"{mark} {int(t//60)}:{int(t%60):02d}  {short(dname):16} killed by {src} ({spell}, {amt:,})")
    if n == 0:
        print("   (no player deaths)")


def cmd_player(path, char, name, pull=None, **_):
    enc = pick_encounter(path, name, pull)
    dur = (enc["end"] - enc["start"]).total_seconds()
    start, end = enc["start"], enc["end"]
    pet2owner = build_pet_owner(path)
    # owner guid(s) for this char
    owner_guids = {g for g, (gg, nm) in pet2owner.items()}  # placeholder
    spells = defaultdict(lambda: {"dmg": 0, "hits": 0, "crits": 0, "max": 0})
    heal_spells = defaultdict(lambda: {"eff": 0, "over": 0, "hits": 0})
    total_dmg = total_heal = 0
    char_l = char.lower()
    # resolve which guids belong to char (player + their pets)
    my_pets = {pg for pg, (og, on) in pet2owner.items() if short(on).lower() == char_l}
    for ev in iter_events(path):
        if ev.ts is None or ev.ts < start or ev.ts > end:
            continue
        if ev.name in DAMAGE_EVENTS:
            d = dmg_fields(ev)
            if not d:
                continue
            guid, nm, amt, crit, spell = d
            mine = short(nm).lower() == char_l and guid.startswith("Player-")
            pet = guid in my_pets
            if not (mine or pet):
                continue
            label = spell if mine else f"{spell} (pet)"
            s = spells[label]
            s["dmg"] += amt; s["hits"] += 1; s["crits"] += int(crit)
            s["max"] = max(s["max"], amt)
            total_dmg += amt
        elif ev.name in HEAL_EVENTS:
            h = heal_fields(ev)
            if not h:
                continue
            guid, nm, eff, over, crit, spell = h
            if short(nm).lower() != char_l or not guid.startswith("Player-"):
                continue
            hs = heal_spells[spell]
            hs["eff"] += eff; hs["over"] += over; hs["hits"] += 1
            total_heal += eff
    res = "KILL" if enc["kill"] else "wipe"
    print(f"\n=== {char} on {enc['name']} (pull #{enc['pull']}, {res}, {fmt_dur(start, end)}) ===")
    if total_dmg:
        print(f"\nDamage: {total_dmg:,}  |  {total_dmg/dur:,.0f} DPS")
        print(f"   {'spell':28} {'damage':>11} {'%':>5} {'hits':>5} {'crit%':>6} {'max':>8}")
        print("   " + "-" * 68)
        for sp, s in sorted(spells.items(), key=lambda kv: kv[1]["dmg"], reverse=True):
            critpct = 100 * s["crits"] / s["hits"] if s["hits"] else 0
            print(f"   {sp:28} {s['dmg']:>11,} {100*s['dmg']/total_dmg:>4.0f}% "
                  f"{s['hits']:>5} {critpct:>5.0f}% {s['max']:>8,}")
    if total_heal:
        print(f"\nHealing (effective): {total_heal:,}  |  {total_heal/dur:,.0f} HPS")
        print(f"   {'spell':28} {'eff heal':>11} {'overheal%':>10} {'casts':>6}")
        print("   " + "-" * 60)
        for sp, hs in sorted(heal_spells.items(), key=lambda kv: kv[1]["eff"], reverse=True):
            opct = 100 * hs["over"] / (hs["eff"] + hs["over"]) if (hs["eff"] + hs["over"]) else 0
            print(f"   {sp:28} {hs['eff']:>11,} {opct:>9.0f}% {hs['hits']:>6}")
    if not total_dmg and not total_heal:
        print(f"   No events found for {char!r} in this fight. Check the name (short form, e.g. 'Celions').")


def cmd_pull(path, name, pull=None, **_):
    """Inferred pull/aggro view. Threat isn't logged; we infer from first damage
    on the boss and from who the boss auto-attacks (its melee target)."""
    enc = pick_encounter(path, name, pull)
    start, end = enc["start"], enc["end"]
    boss = enc["name"]
    first_on_boss = None
    melee_segments = []  # (t_seconds, victim) when boss's melee target changes
    cur_target = None
    for ev in iter_events(path):
        if ev.ts is None or ev.ts < start or ev.ts > end:
            continue
        if ev.name in DAMAGE_EVENTS:
            src_name, dst_name = ev.params[1], ev.params[5]
            # first damage dealt TO the boss by a player
            if first_on_boss is None and dst_name == boss and ev.params[0].startswith("Player-"):
                d = dmg_fields(ev)
                spell = "Melee" if ev.name.startswith("SWING") else (ev.params[9] if len(ev.params) > 9 else "?")
                first_on_boss = (short(src_name), spell, (ev.ts - start).total_seconds())
            # boss auto-attacking someone => that someone holds aggro
            if ev.name.startswith("SWING") and src_name == boss and dst_name.strip():
                if short(dst_name) != cur_target:
                    cur_target = short(dst_name)
                    melee_segments.append(((ev.ts - start).total_seconds(), cur_target))
    print(f"\n{boss} (pull #{enc['pull']}) - pull & aggro  [inferred, threat is not logged]")
    if first_on_boss:
        who, spell, t = first_on_boss
        print(f"\n   Pull started by: {who}  ({spell} at {t:.1f}s into the encounter)")
    else:
        print("\n   Could not identify the opening hit on the boss.")
    print("\n   Boss melee target over time (~= who held aggro):")
    if not melee_segments:
        print("      (boss did no melee swings, or target never resolved)")
    for t, victim in melee_segments[:40]:
        print(f"      {int(t//60)}:{int(t%60):02d}  -> {victim}")
    if len(melee_segments) > 40:
        print(f"      ... (+{len(melee_segments) - 40} more target changes)")


def cmd_compare(path, other, name, metric="dps", me=None, **_):
    def collect(p):
        enc = pick_encounter(p, name)
        dur = (enc["end"] - enc["start"]).total_seconds()
        out = {}
        if metric == "hps":
            for ev in iter_events(p):
                if ev.ts is None or ev.ts < enc["start"] or ev.ts > enc["end"] or ev.name not in HEAL_EVENTS:
                    continue
                h = heal_fields(ev)
                if h and h[0].startswith("Player-"):
                    out[short(h[1])] = out.get(short(h[1]), 0) + h[2]
        else:
            pet2owner = build_pet_owner(p)
            for disp, dmg, _petd in [(v[0], v[1], v[2]) for v in _collect_damage(p, enc, pet2owner).values()]:
                out[short(disp)] = out.get(short(disp), 0) + dmg
        return enc, dur, {k: v / dur for k, v in out.items()}

    encA, durA, A = collect(path)
    encB, durB, B = collect(other)
    print(f"\nCompare {metric.upper()} on {name}")
    print(f"   A = {path}  (pull #{encA['pull']}, {fmt_dur(encA['start'], encA['end'])})")
    print(f"   B = {other} (pull #{encB['pull']}, {fmt_dur(encB['start'], encB['end'])})")
    print(f"\n   {'player':22} {'A':>9} {'B':>9} {'Δ':>9} {'Δ%':>7}")
    print("   " + "-" * 60)
    names = sorted(set(A) | set(B), key=lambda n: A.get(n, 0), reverse=True)
    for n in names:
        a, b = A.get(n, 0), B.get(n, 0)
        delta = a - b
        pct = (100 * delta / b) if b else 0
        mark = ">>" if me and n.lower() == me.lower() else "  "
        print(f"{mark} {n:22} {a:>9,.0f} {b:>9,.0f} {delta:>+9,.0f} {pct:>+6.0f}%")


# ---------------------------------------------------------------------------
def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    path, cmd, rest = argv[1], argv[2], argv[3:]
    # pull global/option flags out of rest
    opts = {"pull": None, "csv_path": None, "me": None, "metric": "dps"}
    pos = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--pull":
            opts["pull"] = int(rest[i + 1]); i += 2
        elif a == "--csv":
            opts["csv_path"] = rest[i + 1]; i += 2
        elif a == "--me":
            opts["me"] = rest[i + 1]; i += 2
        elif a == "--metric":
            opts["metric"] = rest[i + 1]; i += 2
        else:
            pos.append(a); i += 1

    if cmd == "encounters":
        cmd_encounters(path)
    elif cmd == "dps":
        cmd_dps(path, pos[0], pull=opts["pull"], csv_path=opts["csv_path"], me=opts["me"])
    elif cmd == "hps":
        cmd_hps(path, pos[0], pull=opts["pull"], csv_path=opts["csv_path"], me=opts["me"])
    elif cmd == "deaths":
        cmd_deaths(path, pos[0], pull=opts["pull"], me=opts["me"])
    elif cmd == "player":
        cmd_player(path, pos[0], pos[1], pull=opts["pull"])
    elif cmd == "pull":
        cmd_pull(path, pos[0], pull=opts["pull"])
    elif cmd == "compare":
        cmd_compare(path, pos[0], pos[1], metric=opts["metric"], me=opts["me"])
    else:
        print(f"Unknown command: {cmd}\n")
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
