-- Скрытая координация и накрутка объёма: признаки на запуск.
-- Плейсхолдеры как в census.sql: {D0} {D1} {D3}
with
cr as (
  select mint, coalesce(creator, "user") as creator, evt_block_slot as c_slot, evt_block_time as c_time
  from pumpdotfun_solana.pump_evt_createevent
  where evt_block_time >= timestamp '{D0}' and evt_block_time < timestamp '{D1}'
    and coalesce(quote_mint, '11111111111111111111111111111111') = '11111111111111111111111111111111'
),
tr as (
  select t.mint, t."user" as u, t.evt_tx_id as tx, t.evt_outer_executing_account as prog,
         t.evt_block_slot - cr.c_slot as rs,
         to_unixtime(t.evt_block_time) - to_unixtime(cr.c_time) as rt,
         coalesce(t.is_buy, t.isBuy) as is_buy,
         cast(coalesce(t.sol_amount, t.solAmount) as double) / 1e9 as sol,
         cast(coalesce(t.token_amount, t.tokenAmount) as double) / 1e6 as tok,
         (t."user" = cr.creator) as is_cr
  from pumpdotfun_solana.pump_evt_tradeevent t
  join cr on cr.mint = t.mint
  where t.evt_block_time >= timestamp '{D0}' and t.evt_block_time < timestamp '{D3}'
),
-- атомарный отмыв: один кошелёк и покупает, и продаёт в одной транзакции
atx as (
  select mint, tx, u, sum(sol) as vol
  from tr group by 1, 2, 3
  having bool_or(is_buy) and bool_or(not is_buy)
),
atom as (select mint, count(*) as atomic_tx, sum(vol) as atomic_vol from atx group by 1),
-- профиль кошелька в токене
wl as (
  select mint, u, count(*) as n, sum(sol) as vol,
    sum(case when is_buy then sol else 0 end) as b_sol,
    sum(case when is_buy then 0 else sol end) as s_sol,
    sum(case when is_buy then tok else -tok end) as net_tok,
    sum(case when is_buy then tok else 0 end) as b_tok,
    min(rt) as first_rt, min(rs) as first_rs, bool_or(is_cr) as is_cr
  from tr group by 1, 2
),
wash as (
  select mint,
    sum(vol) as tot_vol,
    sum(case when n >= 4 and b_sol > 0 and abs(b_sol - s_sol) <= 0.1 * b_sol
              and abs(net_tok) <= 0.05 * b_tok then vol else 0 end) as rt_vol,
    count(case when n >= 4 and b_sol > 0 and abs(b_sol - s_sol) <= 0.1 * b_sol
                and abs(net_tok) <= 0.05 * b_tok then 1 end) as rt_wallets,
    count(case when b_sol > 0 and b_sol < 0.01 and s_sol = 0 then 1 end) as dust_holders,
    count(case when b_sol > 0 then 1 end) as buyers,
    max(n) as max_trades_one_wallet
  from wl group by 1
),
m30 as (
  select mint, count(*) as n_30m, sum(sol) as vol_30m, count(distinct u) as wal_30m,
         avg(case when sol < 0.01 then 1.0 else 0 end) as micro_share_30m
  from tr where rt <= 1800 group by 1
),
-- первые входы «органики»: после слота 0, не дев, первая покупка в первые 5 минут
fb as (
  select tr.mint, tr.u, min_by(tr.sol, tr.rt) as sol1, min(tr.rt) as t1, min_by(tr.prog, tr.rt) as prog1
  from tr join wl on wl.mint = tr.mint and wl.u = tr.u
  where tr.is_buy and not tr.is_cr and wl.first_rs > 0 and tr.rt <= 300
  group by 1, 2
),
fbg as (
  select mint, u, sol1, t1, prog1,
    t1 - lag(t1) over (partition by mint order by t1) as gap,
    count(*) over (partition by mint, round(sol1, 4)) as same_size
  from fb
),
org as (
  select mint,
    count(*) as org_buyers_5m,
    stddev(sol1) / nullif(avg(sol1), 0) as size_cv,
    avg(case when same_size > 1 then 1.0 else 0 end) as dup_size_share,
    avg(case when abs(sol1 - round(sol1, 2)) < 1e-6 then 1.0 else 0 end) as round_size_share,
    stddev(gap) / nullif(avg(gap), 0) as gap_cv,
    avg(case when gap is not null and gap <= 1 then 1.0 else 0 end) as burst_share,
    avg(case when prog1 = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P' then 1.0 else 0 end) as direct_share
  from fbg group by 1
),
pg as (select mint, prog1, count(*) as c from fb group by 1, 2),
pgs as (select mint, count(*) as n_progs, max(c) * 1.0 / sum(c) as top_prog_share from pg group by 1),
flip as (
  select f.mint, count(distinct f.u) as flippers_5m
  from fb f join tr on tr.mint = f.mint and tr.u = f.u and not tr.is_buy and tr.rt <= 300
  group by 1
)
select cr.mint, atom.atomic_tx, atom.atomic_vol,
       wash.tot_vol, wash.rt_vol, wash.rt_wallets, wash.dust_holders, wash.buyers, wash.max_trades_one_wallet,
       m30.n_30m, m30.vol_30m, m30.wal_30m, m30.micro_share_30m,
       org.org_buyers_5m, org.size_cv, org.dup_size_share, org.round_size_share, org.gap_cv, org.burst_share,
       org.direct_share, pgs.n_progs, pgs.top_prog_share, flip.flippers_5m
from cr
left join atom on atom.mint = cr.mint
left join wash on wash.mint = cr.mint
left join m30  on m30.mint  = cr.mint
left join org  on org.mint  = cr.mint
left join pgs  on pgs.mint  = cr.mint
left join flip on flip.mint = cr.mint
