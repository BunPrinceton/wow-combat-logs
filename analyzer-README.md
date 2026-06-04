# wow-log-analyzer

Offline analyzer for WoW Classic / TBC 2.5.x **advanced** combat logs.
Pure Python 3, no dependencies. Reads logs only, never modifies them.

## Where the logs live
On the WoW box: `C:\World of Warcraft\_anniversary_\Logs\WoWCombatLog-*.txt`
(make sure `/combatlog` is on and Advanced Combat Logging is enabled in WoW's
Network options — these logs already have it.)

In the **wow-combat-logs** repo they're gzip-compressed under `logs/` as
`WoWCombatLog-*.txt.gz` (the raw files are >100 MB, over GitHub's limit). The
analyzer opens `.gz` transparently, so you can point any command straight at the
compressed file — no need to unzip first.

## Usage
```
# raw .txt OR gzipped .txt.gz — both work
python wowlogs.py logs/WoWCombatLog-060226_210103.txt.gz encounters
python wowlogs.py <logfile> encounters
python wowlogs.py <logfile> dps     "<boss>" [--pull N] [--csv out.csv] [--me <char>]
python wowlogs.py <logfile> hps     "<boss>" [--pull N] [--csv out.csv] [--me <char>]
python wowlogs.py <logfile> deaths  "<boss>" [--pull N] [--me <char>]
python wowlogs.py <logfile> player  <char> "<boss>" [--pull N]
python wowlogs.py <logfile> pull    "<boss>" [--pull N]
python wowlogs.py <thisweek.log> compare <lastweek.log> "<boss>" [--metric dps|hps] [--me <char>]
```

`<char>` is the short name (e.g. `Celions`, not `Celions-Dreamscythe-US`).

## Cross-platform (works on your Mac too)
Pure Python 3 standard library — nothing to `pip install`. After cloning the
repo on the Mac, drop these files in (or keep them in their own folder beside
`logs/`) and run with `python3`:
```
python3 serve.py logs/WoWCombatLog-060226_210103.txt.gz --me Nazna
# then open http://localhost:8777/
```
The web UI (`serve.py` + `index.html`) and every CLI command read the gzipped
logs directly. On the Mac you don't need the `PYTHONIOENCODING=utf-8` trick that
Windows PowerShell needs for special characters in names.

## Commands
- **encounters** - list every boss pull (kill/wipe, duration, pull #).
- **dps** - damage table; pet damage is rolled into the owner (a `pet%` column shows how much).
- **hps** - effective healing (overheal removed) + overheal %.
- **deaths** - who died, when, and the killing blow.
- **player** - one character: per-spell damage, crit %, hits, biggest hit, plus healing done.
- **pull** - *inferred* pull/aggro view: who landed the first hit on the boss, and who the
  boss was auto-attacking over time (threat itself is NOT in the log).
- **compare** - same boss across two logs (week-over-week): per-player Δ and Δ%.

## How it parses (for hacking on it)
Each line is `timestamp  EVENT,csv,fields...`. Field layout for these 2.5.x advanced logs:
- base (8): src/dst GUID, name, flags, raidflags
- SPELL/RANGE prefix adds 3: spellId, spellName, spellSchool
- advanced block: 18 fields (HP, power, position, level, ...)
- suffix: damage = `amount, base, overkill, school, resisted, blocked, absorbed, crit(+7)`;
  heal = `amount, base, overheal(+2), absorbed, crit(+4)`.

`dmg_fields()` / `heal_fields()` hand back clean tuples, so new stats are easy to add.

## TODO / ideas
- compare: support `--pull` so two pulls in the SAME log can be diffed.
- interrupts / dispels / spell uptime tables.
- a single `report "<boss>"` that prints dps+hps+deaths together.
- export everything to one CSV/JSON per fight for charting in pandas.
