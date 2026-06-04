# Development notes / handoff

Context for picking this project up on any machine (e.g. when swapping between
the Windows WoW box and the Mac). If you're an AI assistant continuing this
work, **read `analyzer-README.md`, `wowlogs.py`, and `serve.py` first**, then
confirm the tool runs against the gzipped logs before adding features.

## What this is
An offline WoW combat-log analyzer. Pure Python 3 standard library — no
`pip install`, nothing leaves the machine. It only ever READS the logs; it never
modifies or deletes the original log files.

## What's in the repo
- `logs/WoWCombatLog-*.txt.gz` — gzipped TBC 2.5.5 **advanced** combat logs
  (SSC + Gruul's Lair, and TK / The Eye). They're gzipped because the raw files
  exceed GitHub's 100 MB per-file limit. Add new ones with `add_log.sh`.
- `wowlogs.py` — the CLI analyzer. Commands: `encounters`, `dps`, `hps`,
  `deaths`, `player`, `pull`, `compare`. Reads `.gz` transparently via
  `_open_log()`, so point commands straight at the compressed file.
- `serve.py` + `index.html` — a local web UI (stdlib `http.server`, dark theme,
  vanilla JS). Click through every encounter; tabs for Damage / Healing /
  Deaths / Pull & Aggro / Player. Accepts **multiple log files** at once and has
  two "Run" selectors above the tabs: pick one run for the normal single-run
  view, or a second run to turn every tab into an A-vs-B comparison (Δ / Δ%).
- `analyzer-README.md` — full docs, including the combat-log field-offset layout
  for hacking on the parser.
- `README.md` — about the *logs* themselves (not the analyzer). Leave it alone.

## Personal context
- Main character is **Nazna** (Elemental Shaman). Default to `--me Nazna`.
- This is a personal, offline tool — keep it that way.

## Run it
```bash
# web UI (one log)
python3 serve.py logs/WoWCombatLog-060226_210103.txt.gz --me Nazna
# web UI comparing runs across two logs (week-over-week)
python3 serve.py logs/WoWCombatLog-060226_210103.txt.gz logs/WoWCombatLog-060326_205851.txt.gz --me Nazna
# then open http://localhost:8777/  (use the two "Run" dropdowns to compare)

# CLI examples
python3 wowlogs.py logs/WoWCombatLog-060226_210103.txt.gz encounters
python3 wowlogs.py logs/WoWCombatLog-060226_210103.txt.gz dps "Lady Vashj" --me Nazna
python3 wowlogs.py logs/WoWCombatLog-060226_210103.txt.gz player Nazna "Lady Vashj"
```
(On Windows PowerShell, set `$env:PYTHONIOENCODING="utf-8"` first so special
characters in names display correctly. Not needed on macOS/Linux.)

## How the parser works (short version)
Each log line is `timestamp  EVENT,csv,fields...`. Field layout for 2.5.x
advanced logs:
- base (8): src/dst GUID, name, flags, raidflags
- SPELL/RANGE prefix adds 3: spellId, spellName, spellSchool
- advanced block: 18 fields (HP, power, position, level, ...)
- suffix: damage = `amount, base, overkill, school, resisted, blocked,
  absorbed, crit(+7)`; heal = `amount, base, overheal(+2), absorbed, crit(+4)`.

`dmg_fields()` / `heal_fields()` return clean tuples, so new stats are easy.
Pet damage is rolled into the owner via `SPELL_SUMMON` (covers warlocks AND
hunter Call Pet). Threat is NOT in the log — the `pull`/aggro views are
*inferred* from first damage on the boss + who the boss auto-attacks.

## Roadmap (build wherever)
1. ~~**"My Night" overview**~~ — DONE. New default **My Night** tab in the web UI:
   for the highlighted character (`--me`), one screen showing DPS + raid rank
   (#/total) on every boss, a bar vs the top dealer, death markers, and summary
   cards (bosses played, kills, deaths, best rank, avg % of top). Click any row
   to jump into that fight's Damage tab. Pure frontend (`index.html`,
   `renderNight()`) — derived from the existing `/api/report` JSON, no parser
   changes. *Not yet mirrored as a CLI command — see idea below.*
2. ~~**Week-over-week comparison in the UI**~~ — DONE. `serve.py` takes multiple
   logs; the two "Run" selectors in `index.html` diff any two runs (same log or
   across logs) on every tab. (CLI `compare --pull` for same-log diffs is still
   open if you want parity on the command line.)
3. CLI parity for "My Night": a `night <char>` command printing the same
   per-boss DPS + rank table. (Web version is done; CLI still TODO — would need a
   single-pass aggregator in `wowlogs.py` to avoid re-scanning the log per fight.)
4. **Flame Shock uptime / cast efficiency** view for tightening Ele parses.
5. Later: interrupts / dispels / spell-uptime tables; one combined
   `report "<boss>"` that prints dps + hps + deaths together.
