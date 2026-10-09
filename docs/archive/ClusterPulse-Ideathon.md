# ClusterPulse

**Real-time cluster forensics for Solana token launches**

**Submission:** Superteam Ukraine Ideathon (Colosseum Crypto World's Fair prep)
**Tracks:** Trading Infrastructure · Developer Infrastructure · Identity, Privacy and Compliance
**Author:** [YOUR NAME / X HANDLE]

---

Existing rug-detection tools perform an autopsy. They tell you a launch was coordinated after the liquidity is already gone. ClusterPulse compresses that blind window from minutes to under a second by pre-indexing the chain's funding graph, so the moment a pool opens we already know whether its first buyers are strangers or the same wallet wearing twenty hats.

## 1. Why now

As this is written, the dominant conversation on crypto Twitter is a widely circulated thread accusing prominent launch operators and analytics providers of concealed bundling and undisclosed conflicts of interest. We take no position on any individual allegation — and that is exactly the point. Claims like these surface every few months, trend for a week, and dissipate without resolution. The evidence is on-chain and public, yet nobody can produce it in a form that ends the argument: by the time a relationship graph is assembled by hand, the discussion has moved on and nothing has been settled.

The market is saying, loudly and in public, that it wants an answer it currently cannot get. ClusterPulse produces precisely that artifact — a timestamped, independently verifiable on-chain record of what a launch's first-block acquisition actually was, generated at the moment it happens rather than reconstructed months later in a thread.

## 2. Background and prior validation

This proposal is written from a working prototype and from launching tokens, not from reading about the problem.

- **Launch-side experience.** I have deployed more than ten tokens on Solana, roughly half of them through launch tooling I wrote myself, plus three further deployments on Robinhood Chain. I have watched automated buyers take the first blocks from the creator's side of the screen rather than read about it.
- **A running data collector.** I built a Python WebSocket collector against the PumpPortal feed (`subscribeNewToken` / `subscribeMigration`), plus a Helius RPC scanner that pulls holder concentration and creator behaviour. It maintains a local creator-history database keyed by deployer address.
- **Algorithmic trading background.** I have built algorithmic trading systems, including order-book microstructure and spread analysis on centralised venues. That is the source of the latency and execution-ordering intuition behind Layer 2, and of the habit of treating a detection rule as something to be measured rather than asserted.

The collector has already produced a measurable picture of the problem. The figures below come from a single uninterrupted ~20-hour collection window (18–19 April 2026) and are reproducible from the raw file:

| Measured on 869 distinct deployers / 2,264 launches | Result |
|---|---|
| Launches that reached Raydium | 8 — 0.35% |
| Deployers with zero successful migrations | 861 of 869 (99.1%) |
| Share of all launches from the top 50 deployers (5.8% of wallets) | 41.5% |
| Consecutive launches by one deployer under 60s apart | 633 of 1,395 pairs (45.4%) |
| …of those, under 10s apart | 330 |
| Deployers reusing an identical ticker | 142 (16.3%), across 717 repeat launches |
| Tickers deployed by 3 or more unrelated wallets | 74 (e.g. "peppe" — 16 wallets, "BTCTALK" — 14) |

**Scope of this evidence, stated plainly:** the per-deployer token list is truncated by the upstream feed, so every count above is a lower bound. The dataset validates the creator-lineage heuristic described in §4. It does not yet contain funding-graph or bundle data — building and validating those two heuristics is the work proposed for the hackathon.

## 3. Problem statement

Tens of thousands of tokens launch on Solana every day through Pump.fun, Raydium LaunchLab, Bags and Moonshot. On my own sample, 0.35% of them reach a real market. For retail the rest are a loss, and it is not mostly bad luck — it is a repeatable operation.

The enemy is not speed. Sniping is public, permissionless and symmetric: anyone can run a fast bot. The enemy is concealment — a deployer who presents a clean new address while secretly holding the float through wallets nobody can connect to him in time.

### The blind window

A serial operator deploys from a fresh wallet with no history, and in the same block acquires 30–60% of supply across ten to twenty sub-wallets funded hours earlier from one transit address. On a terminal this renders as organic demand from many independent buyers. By the time anyone can prove otherwise, the position is distributed into retail bids.

Every tool a trader has today fails in a different way during that window:

- **RugCheck and Solscan** produce a static snapshot of a finished state. They can show that the top ten holders own 55% of supply; they cannot show that fifteen of those "independent" buyers were funded twenty minutes earlier from one parent wallet.
- **Bubblemaps** computes the relationship graph on demand, which takes minutes. Correct, and too late to act on.
- **Terminal heuristics (Photon, BullX, Trojan, GMGN)** score the deployer address itself. My data shows why that fails: a deployer who rotates address and routes supply through a single hop resets his score to clean, and 45% of observed launches come from operators already running at industrial cadence.

The result is that the people with the most capital at risk make their decision in the seconds when the least is knowable.

## 4. Technical approach

Solana has no public mempool — transactions reach the leader directly, and Jito bundles are not visible to third parties before they land. ClusterPulse therefore makes no claim to predict a bundle. It makes a different claim: that everything needed to judge a launch can be computed in advance, so that the instant the transaction lands the verdict is already a lookup rather than a query.

### Layer 1 — Continuous funding-graph indexer

This is the core asset and it runs whether or not anyone is launching. We continuously ingest SOL transfers and account creations chain-wide and maintain a rolling graph of which wallets funded which, two to three hops deep, with transit hubs and known CEX withdrawal accounts labelled.

Building this graph on demand at launch time is impossible inside a useful latency budget — recursive RPC traversal takes seconds. Building it continuously makes the launch-time question O(1): given twenty buyer addresses, do they share a funding ancestor? The answer is in memory before the pool opens.

### Layer 2 — Launch-time evaluation (Geyser / gRPC)

A Geyser stream surfaces pool-initialisation and the first buys as the node processes them. Within roughly 300–800 milliseconds of the block landing we score the launch on three independent signals, deliberately not relying on any single one:

- **Shared funding ancestry.** The proportion of first-slot buyers converging on a common funding root in the pre-built graph. This is the signal that survives address rotation.
- **Execution co-ordination.** Adjacency of the buys within the block, synchrony of wallet preparation timing, and clustering of purchase amounts and priority-fee parameters. A Jito tip transfer is one input here, never the only one — co-ordination achieved through plain priority fees or a non-Jito path must still be caught.
- **Creator lineage.** Matching deploy characteristics against the historical creator database — migration ratio, time-to-first-sell, ticker and metadata reuse, shared transit hubs. This is the heuristic the existing dataset already validates.

### Layer 3 — On-chain attestations (ZK Compression / Light Protocol)

Scores and risk flags (`SHARED_FUNDING_PARENT`, `COORDINATED_EXECUTION`, `SERIAL_DEPLOYER`) are published as compressed on-chain state. Compressed accounts cost two to three orders of magnitude less than regular accounts, which is what makes retaining a permanent record of every launch economically possible at this volume.

This is what makes ClusterPulse infrastructure rather than another dashboard: any on-chain program can read an attestation in a single CPI. A vault can refuse to route capital into a flagged pool, and a smart-order program can abort before execution — without trusting our API, our uptime, or us.

Honest constraint: enforcement through a Token-2022 transfer hook is deliberately out of scope. Arbitrary transfer hooks are not broadly supported across Solana AMM programs today, and a design that depends on them risks producing tokens that cannot trade where they need to. We ship the read path first.

## 5. Target audience

First user, concretely: the trader who currently keeps Axiom, GMGN and RugCheck open in three tabs, sees a launch, and has perhaps four seconds to decide with none of those tabs able to answer the only question that matters.

- **Day-one buyer (B2B): trading terminals and execution bots** — Photon, BullX, Axiom, Trojan, Maestro. They compete on retention, and a user who gets drained on a bundled launch churns. A low-latency cluster feed is a retention feature they cannot build themselves without owning the funding graph.
- **Token teams.** Honest launches currently have no way to prove they are honest. They can point at a block explorer and hope.
- **Traders and call groups (B2C).** A filtered radar: surface only launches with no detected cluster and a deployer with clean history.
- **Protocols and aggregators.** Pool-level scoring for routing and UI suppression of toxic markets.

## 6. Business model

Revenue starts from the data feed, which needs no network effect to be worth paying for on day one.

- **Streaming API (B2B SaaS) — $500–$2,500 / month.** Prioritised WebSocket feed of cluster and co-ordination events for terminals, bots and market makers, priced by latency tier.
- **Clean Launch Certificate (B2B, deployer side).** A signed, on-chain attestation that a launch had no detected hidden cluster, issued at launch and verifiable by anyone. This closes the loop the ecosystem is missing: today the honest deployer and the operator are indistinguishable at T+0, and the honest one pays for that. When reputations are litigated in public threads, a cryptographic receipt issued before the accusation is worth considerably more than a denial issued after it. It also creates the two-sided position without our building a launchpad.
- **Pay-per-query attestations (on-chain).** Programs and vaults pay per verification in SOL or USDC.
- **Pro radar (B2C) — $39–$79 / month.** Telegram and Discord bot with user-defined filters.

Cost of goods is real and worth stating: a production-grade Geyser stream plus chain-wide indexing runs to low four figures a month. The B2B feed covers it from the first few accounts, which is why the API is the entry product and not the consumer bot.

## 7. Competitive landscape

| | RugCheck / Solscan | Bubblemaps | Terminal tags | ClusterPulse |
|---|---|---|---|---|
| Time to verdict | Post-hoc snapshot | Minutes (on-demand graph) | On UI view | Sub-second after block lands |
| Wallet-relationship depth | 1 hop (balances) | Visual transfer graph | 0 (deployer only) | 2–3 hops, pre-indexed |
| Survives address rotation | No | Partially, too late | No | Yes — funding ancestry |
| Co-ordination detection | Indirect | No | No | Multi-signal, not tip-dependent |
| Consumable on-chain | No (centralised API) | No | No (closed data) | Yes — compressed attestations |

The category difference is not accuracy, it is timing and reach. Competitors answer "was this a scam?" correctly and late, to a human, through a website. ClusterPulse answers "is this one now?" to a program, in time for the program to act.

**Independence.** Independence here is a product requirement, not a virtue. Analytics vendors in this category are frequently commercial partners of the venues whose launches they would have to flag, and users are right to discount a verdict from a vendor carrying that exposure. Publishing scores as on-chain attestations removes the question rather than arguing it: the record is verifiable without trusting the analyst, it is timestamped at the moment of the launch, and it cannot be quietly amended afterwards.

**Defensibility.** The funding graph is an accumulating asset, not a feature. Its value is a function of how long it has been running, because it holds the pre-launch funding of wallets that have not launched anything yet. A competitor starting a year later starts with an empty graph and cannot backfill the wallet preparation they did not observe. That is the moat, and it compounds daily.

## 8. Hackathon scope

Four weeks, one engineer, deliberately narrow — Pump.fun and Raydium launches only, detection and read path only, no enforcement layer.

| Week | Deliverable |
|---|---|
| 1 | Geyser ingestion; pool-init and first-buy parsing; extend the existing collector to persist full launch context |
| 2 | Continuous funding-graph indexer, 2 hops, with transit-hub and CEX-withdrawal labelling |
| 3 | Scoring engine combining all three signals; backtest against historical launches already in the database, reporting precision and recall against what RugCheck showed at T+0 |
| 4 | Public API, Telegram bot, and compressed attestations written on devnet with a reference consumer program |

Demo success criterion, fixed in advance: on a held-out set of historical launches, correctly flag co-ordinated acquisition in cases that terminal heuristics scored clean at launch time — with the false-positive rate reported honestly alongside it.

## 9. Why this team

The collector already exists and has produced the dataset in §2. The creator-lineage heuristic is already validated against 2,264 real launches. What remains is the funding graph and the co-ordination scorer, which is a scoped engineering problem rather than a research one — and I have spent enough time on the execution side of Solana launches to know precisely which evasions to build against.
