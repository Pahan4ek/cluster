-- Кто в итоге заработал: ранняя «органика» (первые 5 мин) против поздней розницы (после 5 и 30 мин)
with
cr as (
  select mint, coalesce(creator, "user") as creator, evt_block_slot as c_slot, evt_block_time as c_time
  from pumpdotfun_solana.pump_evt_createevent
  where evt_block_time >= timestamp '{D0}' and evt_block_time < timestamp '{D1}'
    and coalesce(quote_mint, '11111111111111111111111111111111') = '11111111111111111111111111111111'
),
tr as (
  select t.mint, t."user" as u, t.evt_block_slot - cr.c_slot as rs,
         to_unixtime(t.evt_block_time) - to_unixtime(cr.c_time) as rt,
         coalesce(t.is_buy, t.isBuy) as is_buy,
         cast(coalesce(t.sol_amount, t.solAmount) as double) / 1e9 as sol,
         cast(coalesce(t.virtual_sol_reserves, t.virtualSolReserves) as double)
           / cast(coalesce(t.virtual_token_reserves, t.virtualTokenReserves) as double) * 1e6 as mc,
         (t."user" = cr.creator) as is_cr
  from pumpdotfun_solana.pump_evt_tradeevent t
  join cr on cr.mint = t.mint
  where t.evt_block_time >= timestamp '{D0}' and t.evt_block_time < timestamp '{D3}'
),
w as (
  select mint, u, bool_or(is_cr) as is_cr, min(rs) as first_rs, min(rt) as first_rt,
         sum(case when is_buy then -sol else sol end) as net,
         sum(case when is_buy then sol else 0 end) as spent
  from tr group by 1, 2
),
g as (
  select mint,
    sum(case when not is_cr and first_rs > 0 and first_rt <= 300 then net end) as e5_net,
    sum(case when not is_cr and first_rs > 0 and first_rt <= 300 then spent end) as e5_spent,
    max(case when not is_cr and first_rs > 0 and first_rt <= 300 then net end) as e5_max_net,
    count(case when not is_cr and first_rs > 0 and first_rt <= 300 and net > 0 then 1 end) as e5_winners,
    sum(case when not is_cr and first_rt > 300 then net end) as l5_net,
    sum(case when not is_cr and first_rt > 300 then spent end) as l5_spent,
    count(case when not is_cr and first_rt > 300 then 1 end) as l5_n,
    sum(case when not is_cr and first_rt > 1800 then net end) as l30_net,
    sum(case when not is_cr and first_rt > 1800 then spent end) as l30_spent
  from w group by 1
),
px as (
  select mint,
    max_by(mc, rt) filter (where rt <= 300) as mc_5m,
    max_by(mc, rt) filter (where rt <= 1800) as mc_30m,
    max_by(mc, rt) as mc_end,
    max(mc) filter (where rt > 300) as peak_after5
  from tr group by 1
)
select cr.mint, g.e5_net, g.e5_spent, g.e5_max_net, g.e5_winners, g.l5_net, g.l5_spent, g.l5_n,
       g.l30_net, g.l30_spent, px.mc_5m, px.mc_30m, px.mc_end, px.peak_after5
from cr left join g on g.mint = cr.mint left join px on px.mint = cr.mint
