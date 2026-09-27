-- Дыра в истории девов между концом переписи и стартом скорера.
-- Даты поправь под себя: от конца последнего census_* до «сейчас».
--   python retro/dq.py @retro/devgap.sql devgap
--   python -m live.devdb --append dune_out/devgap.csv
select c.mint, coalesce(c.creator, c."user") as creator, c.evt_block_time as c_time, g.g_time
from pumpdotfun_solana.pump_evt_createevent c
left join (
  select mint, min(evt_block_time) as g_time
  from pumpdotfun_solana.pump_evt_completeevent
  where evt_block_time >= timestamp '2026-09-19 00:00:00'
  group by 1
) g on g.mint = c.mint
where c.evt_block_time >= timestamp '2026-09-19 00:00:00'
  and c.evt_block_time <  timestamp '2026-09-28 00:00:00'
