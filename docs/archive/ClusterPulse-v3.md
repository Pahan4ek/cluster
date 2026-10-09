# ClusterPulse: Real-Time Cluster Forensics & Bundle Radar on Solana

**Submission:** Superteam Ukraine Ideathon (Colosseum Crypto World's Fair prep)
**Tracks:** Trading Infrastructure · Developer Tooling · Security & Reputation
**Builder:** [YOUR NAME / X HANDLE]

---

## 1. TL;DR

RugCheck and Bubblemaps run an autopsy. By the time Bubblemaps has computed its transfer bubbles or RugCheck has updated top-holder stats, the deployer and his wallets have distributed the float into retail bids and pulled liquidity.

ClusterPulse compresses that blind window to under a second. Instead of crawling the chain after a launch begins, we continuously pre-index the chain's funding graph and wallet behavioural history. When a pool opens on Pump.fun or Raydium, we already know whether the first fifteen buyers are strangers to each other, or one operator behind burner wallets that were synthetically warmed and funded through transit hubs.

---

## 2. Why now

Spend ten minutes on crypto Twitter today: much of the timeline is an argument about launchpads, insider bundling and undisclosed conflicts of interest. People post manual screenshot "proofs" days after the dump, everyone disagrees, nothing is settled, and retail has already taken the loss.

We take no position on any individual allegation — that is precisely the point. Claims like these surface every few months, trend for a week and dissipate without resolution. The evidence is on-chain and public, yet nobody can produce it in a form that ends the argument. The market is asking loudly for an answer it currently cannot get.

ClusterPulse produces exactly that artifact: an objective, timestamped, independently verifiable on-chain record generated at the moment of the launch rather than reconstructed later in a thread.

---

## 3. Background and prior validation

This proposal comes from building trading systems and launching tokens, not from reading about the problem.

- **Launch-side experience.** I have deployed more than ten tokens on Solana, roughly half of them through launch tooling I wrote myself, plus three further deployments on Robinhood Chain. I have watched sniper bots and bundle acquisition hit the pool in block 0 from the creator's side of the screen.
- **A running data collector.** I built a Python WebSocket collector streaming from PumpPortal (`subscribeNewToken` / `subscribeMigration`) combined with a Helius RPC scanner. It logs creator histories, holder distribution and migration rates continuously into a local database keyed by deployer address.
- **Algorithmic trading background.** I have built trading systems analysing L2 order-book depth, micro-spreads and imbalance on centralised venues. That is where the working intuition for latency, slippage and execution ordering comes from, and the habit of treating a detection rule as something to be measured rather than asserted.

The collector has already produced a measurable picture of the problem. The figures below come from one uninterrupted ~20-hour window (18–19 April 2026) and are reproducible from the raw `dev_history.json`:

| Measured on 869 unique deployers / 2,264 launches | Value |
|---|---|
| Launches that reached Raydium (bonded) | **8 — 0.35%** |
| Deployers with zero successful migrations | **861 of 869 (99.1%)** |
| Share of all launches from the top 50 deployers (5.8% of wallets) | **41.5%** |
| Consecutive launches by one deployer under 60s apart | **633 of 1,395 pairs (45.4%)** |
| …of those, under 10s apart | **330** |
| Deployers reusing an identical ticker | **142 (16.3%), across 717 launches** |
| Tickers launched by 3+ supposedly unrelated wallets | **74** (e.g. "peppe" — 16 wallets, "BTCTALK" — 14) |

**What this shows:** 99.65% of launches never reach a real market, and over 40% of all launches in the sample come from an industrial-scale farm of serial operators rotating burner addresses and spamming identical meta tickers.

**Scope of this evidence, stated plainly.** The per-deployer token list is truncated by the upstream feed, so every count above is a lower bound. This dataset validates the deployer-lineage heuristic in §5. It does not contain funding-graph or behavioural telemetry — building and validating the synthetic-warmup and delta-neutral detectors is the work proposed for the hackathon, not something already demonstrated.

---

## 4. Problem statement: the evasion game

The problem is not plain sniping. Fast snipers are symmetric and open — anyone can run a fast bot. The problem is concealment, and it operates on four layers, each of which defeats a different existing tool.

1. **Address rotation and transit laundering.** Fresh deployer address every launch; ten to twenty sub-wallets funded through transit hubs or CEX sub-accounts so no direct link exists.
2. **Synthetic warmed wallets.** Operators know the basic checks are `tx_count` and wallet age. They run warmup scripts for days beforehand: WSOL wrap/unwrap cycles, fifty-cent USDC swaps on Jupiter, dust passed between dead tokens. The result is a rich-looking history carrying **zero financial risk and zero net delta** — built for no purpose other than to make a terminal label the wallet an experienced organic trader.
3. **Delta-neutral cross-wallet collusion.** Two or more wallets with **no on-chain transfers between them**, funded from separate CEX accounts, enter the same token together. One buys aggressively to trigger momentum alerts on Photon and DexScreener; the other sells early cheap supply into that momentum. Net directional exposure for the operator is near zero, but the tape shows an explosive organic breakout.
4. **Coordinated bundle acquisition.** In slot 0 the operator takes 30–60% of the bonding curve across the prepared wallets, waits for the FOMO, distributes the float, and repeats several times a day.

### Why existing tools fail

- **RugCheck / Solscan** produce a static post-factum snapshot. They see top-10 holders. They cannot see that those wallets share a funding parent, and they are fully fooled by zero-risk warmup activity.
- **Bubblemaps** tracks *direct token transfers between addresses*. If two wallets never send anything to each other and only ever touch the liquidity pool, Bubblemaps will never link them — by construction, not by oversight. Layer 3 above is invisible to it.
- **Terminal tags (Photon, BullX, Trojan, GMGN)** score the deployer address alone. One hop, or a warmed burner, resets the score to clean. My own data shows 45% of observed launches already come from operators running at that cadence.

---

## 5. Technical approach

Solana has no public mempool — Gulf Stream pushes transactions straight to the leader, and Jito bundles are not visible to third parties before they land. Anyone claiming to predict a bundle before it hits the block is selling snake oil. ClusterPulse makes a different claim: everything needed to judge a launch can be computed *in advance*, so that at the moment of landing the verdict is an O(1) memory lookup rather than a query.

### Layer 1 — Continuous graph and behavioural indexer (background)

Running constantly, independent of any launch:

- **Structural lineage.** Two to three hops of funding roots chain-wide, labelling transit hubs, mixer exits and CEX distribution wallets.
- **Synthetic warmup fingerprinting.** Flagging wallets whose history is dominated by zero-risk churn — high WSOL wrap/unwrap ratio, instantaneous round-trip micro-swaps, cron-like transaction intervals.
- **Delta-neutral pair detection.** Indexing wallet pairs that repeatedly co-occur in the same low-cap tokens with opposing, balanced buy/sell volume.

Building any of this on demand at launch time is impossible inside a useful latency budget — recursive RPC traversal takes seconds. Building it continuously is what makes the launch-time question instant.

### Layer 2 — Live slot scoring (Geyser / gRPC)

Within roughly 300–800 ms of pool initialisation, the first wave of buyers is evaluated against the pre-indexed state on four independent signals, deliberately not relying on any one of them:

- **Funding-tree intersection** — proportion of first-slot buyers converging on a common funding origin. This is the signal that survives address rotation.
- **Synthetic history score** — proportion of early buyers whose on-chain history is algorithmic warmup rather than real trading.
- **Execution synchronicity** — adjacency within the block, identical priority-fee configuration, mirrored timing. A Jito tip transfer is one input here, never the only one: coordination achieved through plain priority fees or a non-Jito path must still be caught.
- **Deployer lineage** — metadata and deploy-pattern matching against the existing database of 2,264 launches.

### Layer 3 — On-chain compressed attestations (Light Protocol / ZK Compression)

The verdict and its flags (`CLUSTERED_ACQUISITION`, `SYNTHETIC_WARMUP`, `DELTA_NEUTRAL_PAIR`, `SERIAL_DEPLOYER`) are committed on-chain as ZK-compressed state.

The cost argument is arithmetic, not marketing: at Solana's rent-exempt minimum, a small standard account costs roughly 0.0027 SOL, so retaining 30,000 launch records a day would run to about 80 SOL per day in rent alone. Compressed state is two to three orders of magnitude cheaper, which is the difference between a permanent public record and a database we ask you to trust.

Any on-chain smart vault, limit-order router or copy-trading contract can then read the attestation in a single CPI call, without depending on our API, our uptime, or us.

**Deliberate constraint.** We do not use custom Token-2022 transfer hooks. Arbitrary hooks are not broadly supported across Solana AMM programs and generally require per-protocol support or whitelisting, so a design that depends on them risks producing tokens that cannot trade where they need to. We build the read and data layer first.

---

## 6. Target audience

**First user, concretely:** the trader with Axiom, GMGN and RugCheck open in three tabs, who sees a launch and has about four seconds to decide, while none of those tabs can answer the only question that matters.

- **B2B, day one — trading terminals and execution bots** (Photon, BullX, Trojan, Maestro, Axiom). Users churn after being dumped on five times in a row. A low-latency cluster feed is a retention feature they cannot build without owning the funding graph.
- **Legitimate token creators.** Honest founders currently have no way to prove they did not bundle half their supply. We give them a verifiable on-chain receipt showing zero detected clusters at T+0, without doxxing anyone.
- **Traders and call groups (B2C).** Telegram alerts that filter out clustered launches and surface only clean deployers.
- **DEXs and aggregators.** Pool-level risk tagging for routing and UI suppression of toxic markets.

---

## 7. Business model

1. **Low-latency streaming API (B2B SaaS) — $500–$2,500 / month.** Raw WebSocket cluster feed for terminals, market makers and prop bots, priced by latency tier.
2. **Clean Launch Certificate.** A verification fee paid by legitimate deployers for an on-chain attestation they can show their community. When reputations are litigated in public threads, a cryptographic receipt issued *before* the accusation is worth considerably more than a denial issued after it. This also creates the two-sided position without our building a launchpad.
3. **On-chain micro-fees.** Pay-per-query in SOL or USDC for contracts checking pool safety.
4. **Pro Telegram radar (B2C) — $39–$69 / month.**

Cost of goods is real and worth stating: a production-grade Geyser stream plus chain-wide indexing runs to low four figures a month. The B2B feed covers it from the first few accounts, which is why the API is the entry product and not the consumer bot.

---

## 8. Competitive landscape

| | RugCheck | Bubblemaps | Photon / BullX tags | **ClusterPulse** |
|:---|:---|:---|:---|:---|
| Speed | Post-launch snapshot | Minutes (on-demand graph) | Instant (UI view) | **Sub-second after block lands** |
| Graph depth | 1 hop (balances) | Multi-hop visual | 0 hops (deployer only) | **2–3 hops, pre-indexed** |
| Bypassed by a fresh wallet? | Yes | Yes | Yes | **No — catches the funding parent** |
| Synthetic warmup detection | No (fooled by `tx_count`) | No | No | **Yes — zero-delta churn filter** |
| Delta-neutral collusion | No | No — requires direct transfers | No | **Yes — paired behavioural pattern** |
| Bundle / execution forensics | No | No | No | **Yes — multi-signal, not tip-dependent** |
| Readable on-chain | No | No | No | **Yes — compressed attestation via CPI** |

**Independence.** Independence here is a product requirement, not a virtue. Analytics vendors in this category are frequently commercial partners of the venues whose launches they would have to flag, and users are right to discount a verdict from a vendor carrying that exposure. Publishing scores as on-chain attestations removes the question rather than arguing it: the record is verifiable without trusting the analyst, it is timestamped at the launch, and it cannot be quietly amended afterwards.

**Defensibility.** The funding and behavioural graph is an accumulating asset, not a feature. Its value is a function of how long it has been running, because it holds the pre-launch preparation of wallets that have not launched anything yet — the warmup scripts running right now for a token that opens next month. A competitor starting a year later begins with an empty graph and cannot backfill activity they did not observe in real time. That is the moat, and it compounds daily.

---

## 9. Hackathon scope — 4 weeks, 1 builder

Deliberately narrow: Pump.fun and Raydium launches only, detection and read path only, no enforcement layer.

| Week | Deliverable |
|---|---|
| 1 | Geyser ingestion pipeline; pool-init and slot-0 transaction parsing; extend the existing collector to persist full launch context |
| 2 | In-memory funding-graph indexer, 2 hops, with transit-hub and CEX tagging; first warmup heuristic (WSOL churn ratio) |
| 3 | Multi-signal scoring engine — funding trees, execution clustering, delta-neutral pairs; backtest against the 2,264 historical launches |
| 4 | Public WebSocket API, Telegram alert bot, and compressed attestations on devnet with a reference consumer program |

**Demo success criterion, fixed in advance:** on a held-out set of historical launches, correctly flag coordinated acquisition in cases that terminal heuristics scored clean at launch time — with the false-positive rate reported honestly alongside it. A detector that flags everything is not a detector.

---

## 10. Why me

I am not pitching an abstract thesis. The collector is already running, the 2,264-launch database already exists, and the deployer-lineage heuristic is already validated against it. What remains is the funding graph and the behavioural scorer: a scoped engineering problem, not a research one. I know which evasions to build against because I have spent enough time on the execution side of Solana launches to have seen them work.
