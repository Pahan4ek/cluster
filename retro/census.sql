-- Перепись запусков pump.fun за день + исходы на кривой + признаки первых слотов.
-- Плейсхолдеры: {D0} начало дня, {D1} конец дня, {D3} конец окна сделок (D0+3д),
--               {H30} D0-30мин (для соседей по тикеру), {H30E} D1+30мин, {P30} D0-30д (история дева)
with
cr_all as (
  select mint, coalesce(creator, "user") as creator, name, symbol,
         evt_block_slot as c_slot, evt_block_time as c_time,
         coalesce(is_mayhem_mode, false) as mayhem,
         coalesce(quote_mint, '11111111111111111111111111111111') as quote_mint
  from pumpdotfun_solana.pump_evt_createevent
  where evt_block_time >= timestamp '{P30}' and evt_block_time < timestamp '{H30E}'
),
cr as (
  select * from cr_all
  where c_time >= timestamp '{D0}' and c_time < timestamp '{D1}'
    and quote_mint = '11111111111111111111111111111111'   -- только котировка в SOL
),
grad as (
  select mint, min(evt_block_time) as g_time
  from pumpdotfun_solana.pump_evt_completeevent
  where evt_block_time >= timestamp '{P30}'
  group by 1
),
tr as (
  select t.mint, t."user" as u, t.evt_tx_signer as signer, t.evt_tx_id as tx,
         t.evt_block_slot - cr.c_slot as rs,
         to_unixtime(t.evt_block_time) - to_unixtime(cr.c_time) as rt,
         coalesce(t.is_buy, t.isBuy) as is_buy,
         cast(coalesce(t.sol_amount, t.solAmount) as double) / 1e9 as sol,
         cast(coalesce(t.token_amount, t.tokenAmount) as double) / 1e6 as tok,
         cast(coalesce(t.virtual_sol_reserves, t.virtualSolReserves) as double)
           / cast(coalesce(t.virtual_token_reserves, t.virtualTokenReserves) as double) * 1e6 as mc,
         cast(coalesce(t.creator_fee, 0) as double) / 1e9 as cfee,
         (t."user" = cr.creator) as is_cr
  from pumpdotfun_solana.pump_evt_tradeevent t
  join cr on cr.mint = t.mint
  where t.evt_block_time >= timestamp '{D0}' and t.evt_block_time < timestamp '{D3}'
),
bots as (
  -- инфраструктура, а не розница: агент Mayhem, объёмные и снайперские боты.
  -- Кошелёк, торговавший в 300+ запусках дня (~1% всех), в метку не идёт
  select u from tr group by u having count(distinct mint) >= 300
),
w as (
  select tr.mint, tr.u, bool_or(is_cr) as is_cr, bool_or(b.u is not null) as is_bot,
         sum(case when is_buy then -sol else sol end) as net_sol,
         sum(case when is_buy then sol else 0 end) as spent,
         min(rs) as first_rs,
         bool_or(signer <> tr.u) as copaid
  from tr left join bots b on b.u = tr.u
  group by 1, 2
),
agg as (
  select mint,
    count(*) as n_tr,
    count(distinct u) as n_wal,
    max(mc) as peak_mc,
    max_by(rt, mc) as t_peak,
    max(rt) as t_last,
    sum(cfee) as creator_fees,
    count(distinct case when rs = 0 and not is_cr and is_buy then u end) as s0_buyers,
    sum(case when rs = 0 and is_buy then tok else 0 end) / 1e9 as s0_supply,
    sum(case when rs = 0 and is_cr and is_buy then tok else 0 end) / 1e9 as dev_supply,
    sum(case when rs <= 2 and is_buy then tok else 0 end) / 1e9 as s2_supply,
    sum(case when rt <= 10 and is_buy then tok when rt <= 10 then -tok else 0 end) / 1e9 as net_supply_10s,
    count(distinct case when rs <= 5 and signer <> u then u end) as copaid_w5,
    count(distinct case when rt <= 10 then u end) as n_wal_10s,
    count(case when rt <= 60 then 1 end) as n_tr_60s,
    min(case when is_cr and not is_buy then rt end) as dev_first_sell_t,
    max(case when rt <= 60 then mc end) as mc_60s
  from tr group by 1
),
txm as (
  select mint, tx, count(distinct u) as nu from tr where rs <= 5 group by 1, 2
),
mt as (
  select mint, count(*) as multi_tx, max(nu) as max_wal_per_tx from txm where nu > 1 group by 1
),
g as (
  select mint,
    sum(case when is_cr then net_sol end) as dev_net,
    sum(case when not is_cr and first_rs = 0 then net_sol end) as s0_net,
    sum(case when not is_cr and first_rs between 1 and 5 then net_sol end) as early_net,
    sum(case when not is_cr and not is_bot and first_rs > 5 then net_sol end) as rest_net,
    sum(case when not is_cr and not is_bot and first_rs > 5 then spent end) as rest_spent,
    count(case when not is_cr and not is_bot and first_rs > 5 then 1 end) as rest_n,
    count(case when not is_cr and not is_bot and first_rs > 5 and net_sol > 0 then 1 end) as rest_winners,
    sum(case when net_sol > 0 then net_sol else 0 end) as total_profit,
    sum(case when copaid and not is_cr then net_sol end) as copaid_net
  from w group by 1
),
sib as (
  select a.mint,
    count(case when b.c_time < a.c_time then 1 end) as sym_before,
    count(case when b.c_time < a.c_time and b.creator <> a.creator then 1 end) as sym_before_other,
    count(case when b.c_time > a.c_time and b.creator <> a.creator then 1 end) as sym_after_other,
    count(case when b.creator = a.creator then 1 end) as sym_same_creator,
    max(case when gb.mint is not null and b.creator <> a.creator then 1 else 0 end) as sib_graduated
  from cr a
  join cr_all b
    on lower(trim(b.symbol)) = lower(trim(a.symbol)) and b.mint <> a.mint
   and b.c_time between a.c_time - interval '30' minute and a.c_time + interval '30' minute
  left join grad gb on gb.mint = b.mint
  group by 1
),
hist as (
  select a.mint,
    count(p.mint) as dev_prev_30d,
    count(gp.mint) as dev_prev_grad
  from cr a
  join cr_all p on p.creator = a.creator and p.c_time < a.c_time
  left join grad gp on gp.mint = p.mint and gp.g_time < a.c_time
  group by 1
)
select *
from cr
left join grad using (mint)
left join agg  using (mint)
left join mt   using (mint)
left join g    using (mint)
left join sib  using (mint)
left join hist using (mint)
