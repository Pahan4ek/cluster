-- Финансирование и прогрев первых «органических» покупателей.
-- Цель: первые 15 кошельков, купивших после слота 0 (не дев) в первые 5 минут,
-- в запусках, где таких покупателей >= 5.
-- {D0} {D1} день запуска, {D1P} = D1 + 10 мин, {F0} = D0 - 14д (финансирование), {W0} = D0 - 7д (прогрев)
with
cr as (
  select mint, coalesce(creator, "user") as creator, evt_block_slot as c_slot, evt_block_time as c_time
  from pumpdotfun_solana.pump_evt_createevent
  where evt_block_time >= timestamp '{D0}' and evt_block_time < timestamp '{D1}'
    and coalesce(quote_mint, '11111111111111111111111111111111') = '11111111111111111111111111111111'
),
tr as (
  select t.mint, t."user" as u, t.evt_block_slot - cr.c_slot as rs, t.evt_block_time as bt,
         coalesce(t.is_buy, t.isBuy) as is_buy,
         cast(coalesce(t.sol_amount, t.solAmount) as double) / 1e9 as sol,
         (t."user" = cr.creator) as is_cr, cr.creator
  from pumpdotfun_solana.pump_evt_tradeevent t
  join cr on cr.mint = t.mint
  where t.evt_block_time >= timestamp '{D0}' and t.evt_block_time < timestamp '{D1P}'
    and t.evt_block_time <= cr.c_time + interval '300' second
),
w as (
  select mint, u, min(rs) as first_rs, min(bt) as bt1, bool_or(is_cr) as is_cr,
         max(creator) as creator, min_by(sol, bt) as sol1
  from tr where is_buy group by 1, 2
),
lw as (
  select mint, count(*) as n_org from w where first_rs > 0 and not is_cr group by 1
),
tg as (
  select * from (
    select w.mint, w.u, w.bt1, w.sol1, w.creator,
           row_number() over (partition by w.mint order by w.bt1, w.u) as rn
    from w join lw on lw.mint = w.mint
    where w.first_rs > 0 and not w.is_cr and lw.n_org >= 5
  ) where rn <= 15
),
wallets as (select distinct u from tg),
fin as (
  select s.account_to as u, s.account_from as f, s.call_block_time as ft,
         cast(s.lamports as double) / 1e9 as amt
  from system_program_solana.system_program_call_transfer s
  where s.call_block_time >= timestamp '{F0}' and s.call_block_time < timestamp '{D1P}'
    and s.lamports >= 10000000
    and s.account_to in (select u from wallets)
),
fa as (
  select tg.mint, tg.u,
    min(fin.ft) as first_ft, min_by(fin.f, fin.ft) as first_f, min_by(fin.amt, fin.ft) as first_amt,
    max(fin.ft) as last_ft, max_by(fin.f, fin.ft) as last_f, max_by(fin.amt, fin.ft) as last_amt,
    count(fin.ft) as n_in, count(distinct fin.f) as n_funders
  from tg left join fin on fin.u = tg.u and fin.ft < tg.bt1
  group by 1, 2
),
pw as (
  select p."user" as u, p.evt_block_time as pt, p.mint as pm
  from pumpdotfun_solana.pump_evt_tradeevent p
  where p.evt_block_time >= timestamp '{W0}' and p.evt_block_time < timestamp '{D1P}'
    and p."user" in (select u from wallets)
),
warm as (
  select tg.mint, tg.u, count(pw.pt) as prior_trades, count(distinct pw.pm) as prior_mints
  from tg left join pw on pw.u = tg.u and pw.pt < tg.bt1 and pw.pt >= tg.bt1 - interval '7' day
  group by 1, 2
)
select tg.mint, tg.u, tg.rn, to_unixtime(tg.bt1) as bt1, tg.sol1, tg.creator,
       to_unixtime(fa.first_ft) as first_ft, fa.first_f, fa.first_amt,
       to_unixtime(fa.last_ft) as last_ft, fa.last_f, fa.last_amt, fa.n_in, fa.n_funders,
       warm.prior_trades, warm.prior_mints
from tg
left join fa   on fa.mint = tg.mint and fa.u = tg.u
left join warm on warm.mint = tg.mint and warm.u = tg.u
