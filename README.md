# wow-combat-logs

Raid combat logs from **TBC Classic (2.5.5, Anniversary realm)**, auto-captured by the
[Archon](https://www.archon.gg/) app. Stored here gzip-compressed for stats / analysis.

These are the *advanced* combat log format (`COMBAT_LOG_VERSION 9`).

## Contents

| File | Raid | Notes |
|------|------|-------|
| `logs/WoWCombatLog-060226_210103.txt.gz` | **SSC** (Serpentshrine Cavern) + Gruul's Lair | all 6 SSC bosses |
| `logs/WoWCombatLog-060326_205851.txt.gz` | **TK** (Tempest Keep / The Eye) | all 4 bosses |

Main character: **Nazna** (Dreamscythe-US).

> Files are gzipped because the raw SSC log is 135 MB, over GitHub's 100 MB per-file
> limit. Compressed they're ~12 MB and ~8 MB. Originals are untouched in the live
> WoW `Logs/` folder.

## Reading them

No need to decompress to disk — Python reads gzip transparently:

```python
import gzip

with gzip.open("logs/WoWCombatLog-060226_210103.txt.gz", "rt", encoding="utf-8") as f:
    for line in f:
        # each line: "M/D HH:MM:SS.mmm  EVENT,field,field,..."
        ...
```

Or decompress a copy if you'd rather work with raw text:

```bash
gunzip -k logs/WoWCombatLog-060226_210103.txt.gz   # -k keeps the .gz
```

## Line format (quick reference)

```
6/2 21:01:03.123  SPELL_DAMAGE,<srcGUID>,"<srcName>",<srcFlags>,...,<destGUID>,"<destName>",...,<spellId>,"<spell>",<school>,<amount>,...
```

Timestamp (local), two spaces, then a comma-separated event record. Names are
quoted; the leading sub-event token names the event (`SPELL_DAMAGE`,
`SPELL_HEAL`, `SWING_DAMAGE`, `UNIT_DIED`, `ENCOUNTER_START/END`, etc.).

## Updating

Not auto-synced. To refresh with newer raids, re-gzip the latest logs into `logs/`
and commit — see `add_log.sh`.
