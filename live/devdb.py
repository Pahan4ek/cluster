"""devdb.py — история запусков создателей: сколько раз дев запускал за 30 дней
и сколько из этого вышло на AMM. Признаки dev_prev_30d / dev_prev_grad /
dev_grad_rate считаются ровно как в retro/census.sql.

Стартовая база — перепись Dune за месяц (dune_out/census_*.csv.gz):
    python -m live.devdb --seed
Дальше скорер сам дописывает каждый новый запуск и выход на AMM.

ВАЖНО: между концом переписи и стартом скорера есть дыра в истории.
Пока скорер не проработал ~30 дней непрерывно, счётчики у активных
девов занижены. Дыру закрывает retro/devgap.sql + python -m live.devdb --append <csv>.
"""
import csv
import glob
import gzip
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "devdb.sqlite"
WINDOW = 30 * 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (id INTEGER PRIMARY KEY, addr TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS launches (creator_id INTEGER NOT NULL, ts INTEGER NOT NULL,
                                     grad_ts INTEGER, mint TEXT);
CREATE INDEX IF NOT EXISTS idx_launch_creator_ts ON launches(creator_id, ts);
CREATE UNIQUE INDEX IF NOT EXISTS idx_launch_mint ON launches(mint) WHERE mint IS NOT NULL;
"""


class DevDB:
    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.execute("pragma journal_mode=wal")
        self.conn.execute("pragma synchronous=normal")
        self.conn.executescript(SCHEMA)
        self._ids = {}
        self._pending = 0

    def _cid(self, addr, create=True):
        cid = self._ids.get(addr)
        if cid is not None:
            return cid
        row = self.conn.execute("select id from creators where addr=?", (addr,)).fetchone()
        if row:
            cid = row[0]
        elif create:
            cid = self.conn.execute("insert into creators(addr) values (?)", (addr,)).lastrowid
        else:
            return None
        if len(self._ids) > 1_000_000:
            self._ids.clear()
        self._ids[addr] = cid
        return cid

    def history(self, creator, ts):
        """(dev_prev_30d, dev_prev_grad) на момент ts, только прошлые запуски."""
        cid = self._cid(creator, create=False)
        if cid is None:
            return 0, 0
        n, g = self.conn.execute(
            """select count(*), sum(case when grad_ts is not null and grad_ts < ? then 1 else 0 end)
               from launches where creator_id=? and ts < ? and ts >= ?""",
            (ts, cid, ts, ts - WINDOW)).fetchone()
        return n or 0, g or 0

    def add_launch(self, creator, mint, ts):
        cid = self._cid(creator)
        self.conn.execute("insert or ignore into launches(creator_id, ts, mint) values (?,?,?)", (cid, ts, mint))
        self._tick()

    def mark_graduated(self, mint, ts):
        self.conn.execute("update launches set grad_ts=? where mint=? and grad_ts is null", (ts, mint))
        self._tick()

    def _tick(self):
        self._pending += 1
        if self._pending >= 200:
            self.commit()

    def commit(self):
        self.conn.commit()
        self._pending = 0


def _ts(s):
    """'2026-09-01 00:00:12.000 UTC' -> unix-время."""
    if not s:
        return None
    return int(datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())


def seed(path=DB_PATH):
    files = sorted(glob.glob(str(ROOT / "dune_out" / "census_2026-*.csv*")))
    if not files:
        sys.exit("[FATAL] нет dune_out/census_*.csv[.gz] — нечем засеять историю")
    db = DevDB(path)
    have = db.conn.execute("select count(*) from launches").fetchone()[0]
    if have:
        print(f"в базе уже {have:,} запусков — сид пропускаю (удали {path.name}, чтобы пересеять)")
        return
    t0, n = time.time(), 0
    for f in files:
        opener = gzip.open if f.endswith(".gz") else open
        with opener(f, "rt", encoding="utf-8", newline="") as fh:
            rows = []
            # в названиях токенов бывают NUL-байты, csv на них падает
            for r in csv.DictReader(line.replace("\0", "") for line in fh):
                if not r.get("creator") or not r.get("c_time"):
                    continue
                # mint не храним: выходы на AMM за месяц уже известны, а 1 млн адресов — лишние 90 МБ
                rows.append((db._cid(r["creator"]), _ts(r["c_time"]), _ts(r.get("g_time")), None))
            db.conn.executemany("insert or ignore into launches(creator_id, ts, grad_ts, mint) values (?,?,?,?)", rows)
            db.commit()
            n += len(rows)
            print(f"  {Path(f).name}: {len(rows):,}")
    last = db.conn.execute("select max(ts) from launches").fetchone()[0]
    print(f"засеяно {n:,} запусков, {db.conn.execute('select count(*) from creators').fetchone()[0]:,} девов "
          f"за {time.time()-t0:.0f} с; история до {datetime.fromtimestamp(last, timezone.utc):%Y-%m-%d %H:%M} UTC")


def append(csv_path, path=DB_PATH):
    """Дописать запуски из выгрузки retro/devgap.sql (mint, creator, c_time, g_time).
    Повторы по mint отсекает уникальный индекс, поэтому пересечение с живыми данными безопасно."""
    db = DevDB(path)
    opener = gzip.open if str(csv_path).endswith(".gz") else open
    rows = []
    with opener(csv_path, "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(line.replace("\0", "") for line in fh):
            if r.get("creator") and r.get("c_time"):
                rows.append((db._cid(r["creator"]), _ts(r["c_time"]), _ts(r.get("g_time")), r.get("mint") or None))
    before = db.conn.execute("select count(*) from launches").fetchone()[0]
    db.conn.executemany("insert or ignore into launches(creator_id, ts, grad_ts, mint) values (?,?,?,?)", rows)
    # у уже известных минтов мог появиться выход на AMM
    db.conn.executemany("update launches set grad_ts=? where mint=? and grad_ts is null",
                        [(g, m) for _, _, g, m in rows if g and m])
    db.commit()
    added = db.conn.execute("select count(*) from launches").fetchone()[0] - before
    print(f"в выгрузке {len(rows):,}, добавлено {added:,} запусков")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--seed" in sys.argv:
        seed()
    elif "--append" in sys.argv:
        append(sys.argv[sys.argv.index("--append") + 1])
    else:
        print(__doc__)
