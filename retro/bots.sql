-- Кошельки, торговавшие в 300+ запусках pump.fun за день (та же отсечка, что в CTE bots в census.sql).
--   python retro/dq.py @retro/bots.sql bots_2026-09-18
with cr as (
  select mint from pumpdotfun_solana.pump_evt_createevent
  where evt_block_time >= timestamp '2026-09-18 00:00:00' and evt_block_time < timestamp '2026-09-19 00:00:00'
),
tr as (
  select t."user" as u, t.mint, coalesce(t.is_buy, t.isBuy) as is_buy,
         cast(coalesce(t.sol_amount, t.solAmount) as double) / 1e9 as sol
  from pumpdotfun_solana.pump_evt_tradeevent t join cr on cr.mint = t.mint
  where t.evt_block_time >= timestamp '2026-09-18 00:00:00' and t.evt_block_time < timestamp '2026-09-21 00:00:00'
)
select u, count(distinct mint) as n_mints, count(*) as n_tr,
       sum(case when is_buy then sol else 0 end) as spent,
       sum(case when is_buy then -sol else sol end) as net_sol,
       avg(case when is_buy then sol end) as avg_buy
from tr group by u having count(distinct mint) >= 300
order by n_mints desc
