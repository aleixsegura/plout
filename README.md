# Plout

A local personal-finance dashboard. A set of Python exporters pull your data straight from the
sources — DEGIRO (positions, dividends, fees), Revolut (cash + crypto) and public market
data — into plain JSON under `data/`, and a Vite + React app in
`web/` reads that folder live and renders it. It also prepares the Spanish income-tax return
(IRPF): FIFO capital gains, the two-month repurchase rule (art. 33.5 f LIRPF) and a filing pack.

Nothing is uploaded anywhere and no data is committed: the exports stay on your machine.

## Try it without any account

The repo ships with synthetic fixtures in `demo-data/` (regenerate with
`python3 generate_demo_data.py`), so the whole app runs without broker credentials:

```bash
cd web
npm install      # Node 20 — see web/.nvmrc
npm run demo     # http://localhost:5175, serves demo-data/ instead of data/
npm run build:demo   # static build in web/dist-demo/, deployable to any static host
```

## Privacy model

Real data never reaches the repository:

- Every export, credential and cost-basis file is gitignored (`data/`, `.env`, `*.local.*`, …).
- Anything the app needs to know per symbol — sectors, DEGIRO ticker aliases — lives in
  `data/symbols.json`, not in code (`demo-data/symbols.json` shows the shape).
- `scripts/privacy_check.py` fails when a private file is tracked and, on a machine with real
  exports, when any tracked file mentions a symbol found in them. Enable it before every commit
  with `git config core.hooksPath .githooks`; CI runs it too, alongside gitleaks.

## Exporters

| Exporter | Writes | Feeds |
|---|---|---|
| `export_degiro.py` | `positions.json` | Holdings |
| `export_revolut.py` | `revolut.json`, `revolut_trades.json`, `revolut_transactions.json` | Dashboard, Holdings, Transactions |
| `export_dividends.py` | `dividends.json` | Dividends |
| `export_fees.py` | `fees.json` | Tax return (the non-deductible-fees notice) |
| `export_taxes.py` | `taxes.json`, `renta/renta-<year>.{json,md}` | Tax return |
| `export_marketcaps.py` | `marketcaps.json` | The Market Cap column, everywhere (runs itself on `npm run dev`) |
| `export_prices.py` | `prices.json` | Correlation (runs itself on `npm run dev`) |
| `history.py` | `history.json` | The portfolio chart on Holdings |

## Broker exporters

Both exporters write into the same `data/` folder the web app serves, and read credentials/keys from a gitignored `.env` (see `.env.example`).

### DEGIRO

```bash
.venv/bin/pip install -r requirements.txt
python3 export_degiro.py
```

Logs in with `DEGIRO_USERNAME`/`DEGIRO_PASSWORD` from `.env` (approve the in-app confirmation when prompted) and writes portfolio positions and account totals to `data/positions.json`.

DEGIRO's portfolio endpoint prices every position at the *previous* close (today's P/L comes back as exactly 0), so positions are then re-priced from the same vwd quote feed the DEGIRO app itself uses — 15-minute delayed on exchanges the account has no real-time licence for. Each position records where its price came from in `lastPriceSource` (`vwd` or `degiro-snapshot`) and when in `lastPriceAt`. Pass `--no-quotes` to keep DEGIRO's raw previous-close snapshot.

### DEGIRO fees

```bash
.venv/bin/python export_fees.py
```

Pulls the same cash statement as the dividend exporter and keeps only what DEGIRO *charged* the account, split into `transaction`, `connectivity`, `currency` and `other`, into `data/fees.json`. There is no fees page: buy and sell commissions already reach the UI inside the tax return's acquisition and disposal values. What this file adds is the rest — connectivity, custody and FX — which `export_taxes.py` reads back to raise its non-deductible-fees notice. Read it directly for anything beyond that.

Descriptions are localised to your account language, so charges are matched by keyword. A movement only counts as a fee if its description actually says so ("comisión", "coste", "fee", "AutoFX"), which is what keeps DEGIRO's `Cambio de Divisa (Débito)`/`(Crédito)` pairs — the conversion itself, not a charge on it — out of the total. Run with `--debug` to print every distinct movement description next to the category it landed in; if something is mis-bucketed, adjust `FEE_CATEGORIES` / `FEE_TOKENS` in the script.

| Flag | Description |
|---|---|
| `--from YYYY-MM-DD` | Earliest date to scan (default: `2015-01-01`) |
| `--debug` | Print every cash-movement description with its category and exit |
| `--output-dir DIR` | Where the `data/` folder lives (default: current directory) |

### Revolut

Revolut has no personal API, so `export_revolut.py` drives the Revolut web app with Playwright, capturing the app's own internal JSON responses for cash (current + savings/"Boosted" accounts) and crypto balances. Everything is normalized to EUR via Coinbase's public API (no key needed).

Crypto positions carry two prices, because the two answer different questions: `lastPriceEur` drives the portfolio maths, while `lastPrice` (in USD, crypto's conventional quote currency) is what the Holdings table displays — it is the number every price site shows, and it sits ~13% above the EUR one.

One-time setup:

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

Then just run:

```bash
python3 export_revolut.py
```

A browser window opens automatically. **If your session is still active, it captures and closes on its own.** Otherwise, log in there (phone number, passcode, approve the push in the Revolut phone app) and it continues once you're in, writing `data/revolut.json`.

Note: Revolut runs the web app behind Cloudflare and does not keep the session across browser restarts, so the window must be visible (headless is blocked) and you'll typically re-approve a login each run. This is as unattended as a personal Revolut account allows — there is no official API.

| Flag | Description |
|---|---|
| `--login` | Just establish/refresh the session without exporting, then exit |
| `--full` | Refetch the whole transaction history instead of only what is newer than the existing `revolut_transactions.json` |
| `--from YYYY-MM-DD` | Earliest transaction to keep (default: everything Revolut returns) |
| `--no-transactions` | Skip the transactions / interest export |
| `--debug` | Dump every captured Revolut API response to `.revolut-debug/` (gitignored — contains personal financial data). Use this to re-pin `CAPTURE_PATTERNS` in the script if Revolut changes their internal API. |
| `--headless` | Run with no visible window (usually blocked by Cloudflare; expert use) |
| `--output-dir DIR` | Where the `data/` folder lives (default: current directory) |

### Revolut transactions and Boosted interest

The same run also pages the EUR current account's full history and the Boosted account's own
pocket (where the daily interest is credited) into `data/revolut_transactions.json`, the
file behind the **Dashboard** spending/interest cards and the **Transactions** page. The web app
only asks for the latest page; older rows are reached by replaying the app's own call with an
upper timestamp bound (`to=`), tried against `TRANSACTIONS_PAGE_PARAMS` in order — the first
parameter that yields unseen rows is recorded as `pageParam` in the file, so a rename on Revolut's
side surfaces as a warning rather than a silently truncated export. Runs are incremental: rows
older than the file's newest row minus a week are not requested again (`--full` to refetch).

Every leg is classified into a `kind`, which is what decides what counts as spending:

| Kind | Rule |
|---|---|
| `interest` | Boosted interest (`vaults.saving_accounts.interest.gained`, category `interest`) |
| `transfer` | money moved between your own accounts — to/from Boosted or Revolut X, crypto and currency exchanges, transfers to your broker (`TRANSFER_INTERNAL_COUNTERPARTS`) and to/from an account in your own name (the holder's name comes from `/user/current`). Excluded from spending and income. |
| `fee` | explicit `CHARGE`/`FEE` rows (plan subscription) and the fee carried by another row, emitted as its own row so spending stays gross |
| `expense` | card payments and outgoing transfers to other people (Bizum included), under Revolut's own `category` |
| `refund` | card refunds — subtract from that category's spending |
| `income` | top-ups, Bizum and transfers received |
| `other` | anything unrecognised, counted in `ignoredTypes` rather than dropped |

Pending rows and zero-amount card verifications are skipped. `months[]` (spent / income /
interest / fees / net / per-category) and `interest` (daily credits with the savings balance
after each one, and monthly totals) are precomputed so the web app only renders. The
classification rules are pinned by `tests/test_revolut_transactions.py`:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Tax return

```bash
.venv/bin/python export_taxes.py
```

The only exporter that needs a *ledger* rather than a snapshot. `positions.json` shows what you
still hold, so a position sold in full disappears from it entirely, and DEGIRO's own realised P/L
mixes gains with buy fees. So this one rebuilds the history from trades and matches every sale
against its purchases with **FIFO**, which is what article 37.2 LIRPF requires for *valores
homogéneos*, writing `data/taxes.json`.

Sales made in a year are declared in the return filed the following spring: sell something in
2026 and it goes in the 2026 return, filed in 2027. The page has one tab per fiscal year and
keeps tracking the current one as it goes.

Three sources are merged into one FIFO queue per line of homogeneous securities:

| Source | Gives | Notes |
|---|---|---|
| DEGIRO `get_transactions_history` | every buy and sell since the account opened | already denominated in EUR by DEGIRO (`totalInBaseCurrency`), which is what their statement shows |
| `bybit.csv` | crypto bought on Bybit and later moved to Revolut | quoted in USDC, converted at the **ECB reference rate** of each trade date (via `frankfurter.dev`, no key). `Fees` there is charged in the coin bought, so the quantity acquired is `Filled Quantity − Fees` |
| `manual_trades.json` | disposals made outside DEGIRO, staking rewards, stray lots | gitignored and hand-maintained, like `crypto_cost_basis.json` |

Moving crypto between your own wallets is not a disposal, so a Bybit purchase and a later Revolut
sale share one queue — the acquisition date and value stay the original ones.

What it works out for you:

- **Valor de adquisición** = what was paid plus the commissions inherent to the purchase;
  **valor de transmisión** = what came in minus the commissions inherent to the sale. Connectivity
  and custody fees are a real cost but are *not* deductible, and the page says so.
  Expect the commissions here to exceed what `fees.json` holds for the same trades — on a USD
  order they run 0.25–0.38% of the notional, because they include the 0.25% AutoFX charge DEGIRO
  takes inside the exchange rate instead of as a cash movement. `export_fees.py` reads the cash
  statement, so it never sees that part; for tax it counts.
- **Regla de los dos meses** (art. 33.5 f): a loss on listed shares is deferred when homogeneous
  securities were repurchased within two months either side — *proportionally*, so buying back 25
  of the 85 shares sold blocks 25/85 of the loss and the other 60/85 is computable straight away.
  Only shares still held on 31 December count as a repurchase: shares the sale itself consumed
  never restored the position, and a buy-back closed again before year end nets out. Sell the
  blocking shares in a **later** year and the deferred loss is handed back in that year, as its
  own line — it is a separate pérdida patrimonial, so it never touches the figures you type for
  the sale that triggered it. For crypto the DGT's position on the window isn't settled, so it is
  flagged for review rather than applied.
- **Dividends and staking rewards** as *rendimientos del capital mobiliario* — art. 25.1 and 25.2
  respectively, different sections of the form. A staking reward's EUR value on the day it was
  credited does double duty: taxed then, and the acquisition value of those coins when sold.
- **Modelo 721** threshold check on crypto held abroad, and **Modelo 720** on everything else
  that is — cash and securities, measured per block rather than on the total, because each block
  has its own 50.000 € threshold and crypto belongs to the 721, not here. Both are prompts to go
  and check, not verdicts: what decides it is where the custodian sits, not which app you use.
  Plus a savings-base tax estimate, which covers capital gains only — dividends and staking are
  savings income too but are not in that figure.
- A sale no lot covers is never given an invented cost: the uncovered slice is declared at zero
  (the conservative reading) and named in the warnings so you can fix it in `manual_trades.json`.

### The filing pack

Each run also writes `data/renta/renta-<year>.json` and `.md`. The JSON has every figure
field by field under the form's own labels; the Markdown is the brief an agent reads before
touching Renta Web — which section to open, in what order, and three standing rules: invent no
figure, stop and ask if a label doesn't match, never submit the return.

Every disposal entry also carries `feesEur`: the buying commission and the selling one apart,
plus the gross amounts they were folded into. Renta Web's own window has no box for them — the
buying commission is already inside the *valor de adquisición* and the selling one already out of
the *valor de transmisión* — but **Cartera de Valores**, the alternative route for listed shares,
asks for "Gastos de la operación" on every acquisition (AD) and every transmission (TR) and wants
the amount of the operation gross. Both readings are given so neither route needs arithmetic at
the keyboard. The selling commission is charged per order, so it is shared out over the sale's
FIFO parcels pro rata by quantity, adding back up to the cent.

**One entry per sale**, in `entries` — the transmission is what the law taxes, and a sell order
is one alteración patrimonial whose result is worked out internally by FIFO. Since 2015 the
acquisition date does not change the tax (everything goes to the base del ahorro regardless of
holding period; it would only matter for coeficientes de abatimiento, i.e. acquired before
31/12/1994), and the broker's own datos fiscales come per operation too — so splitting a sale by
acquisition date adds lines to type and nothing else. Where several buys funded the sale, the
acquisition date reads `varias` and the dates themselves are in `acquisitionDates`, to be entered
under "valores adquiridos en distintas fechas". The sale date is never `varias`: it is what fixes
the ejercicio.

`parcelEntries` keeps the per-lot breakdown for the case where the screen insists on one
acquisition date per line — same totals, to be typed *instead of* the sale entry, never as well
as. Its ids carry `-lote-` so the two levels can't be confused. The Markdown prints the sale
entries in full and the parcels as one compact table at the end.

Inside both, the staking rewards behind a sale count as a **single acquisition**, marked
`merged`. A year of daily rewards is a year of lots: 62 of them came to 39,56 € in the 2026 pack,
and one line each is more form than the amount deserves. They share the disposal date, so the
only thing the sum loses is the acquisition date; the entry carries the count and the date range,
and `taxes.json` keeps every reward one by one as the audit trail.

Box numbers are deliberately **not** hardcoded as fact — they are renumbered almost every year.
Navigation goes by section titles and field labels, which are stable, and each section carries a
`casillaHint` to verify on screen.

None of this is tax advice: every figure carries the inputs it came from so you (or your advisor)
can check it.

| Flag | Description |
|---|---|
| `--from YYYY-MM-DD` | Earliest date to scan (default `2015-01-01`) |
| `--year YYYY` | Fiscal year for the filing pack (default: the most recent year with data) |
| `--bybit PATH` | Bybit spot-trade export (default `bybit.csv`) |
| `--no-degiro` | Skip the DEGIRO login and build from `bybit.csv` / `manual_trades.json` only |
| `--debug` | Print the normalized trade ledger before aggregating |

## Market caps

Nothing to run: `npm run dev` starts the exporter itself (a small Vite plugin), in the background, and reloads the page if it wrote anything new. It resolves the **Market Cap** column from public quote APIs into `data/marketcaps.json`.

Symbols come from what is already exported (`positions.json`, `revolut.json`); a DEGIRO position's Yahoo symbol is derived from its exchange; ETFs and funds are skipped, as their size is AUM rather than a market cap. Sources are keyless and tried in order:

| Source | Covers |
|---|---|
| Yahoo Finance `v7/finance/quote` | everything, non-US listings included (`VWCE.DE`, `AIR.PA`, …), batched 50 symbols per request |
| `api.nasdaq.com` | US symbols, as the fallback for when Yahoo rate-limits (it often does, with a 429 on the cookie/crumb handshake) |
| CoinGecko | crypto (Revolut's bare `BTC`, `ETH`, …) |

Values are written under every symbol spelling of a company — Yahoo's `VWCE.DE` and DEGIRO's `VWCE` — so a row finds its market cap by whichever it carries. A symbol nothing answered for keeps the value from the previous run — each entry carries its own `at` — so a rate-limited Yahoo never empties the file; a symbol with no entry at all shows "—".

Most dev-server starts cost nothing: `--max-age-hours` makes the exporter exit before its first request when the file is younger than 12 hours *and* already covers every symbol. A stock you just bought has no entry yet, so it refreshes straight away instead of waiting for that window to pass.

You can still run it by hand — it needs no credentials and no browser — which is also how to refresh the column without restarting the dev server:

```bash
.venv/bin/python export_marketcaps.py
```

## Price history

Also run by `npm run dev`, with the same 12-hour `--max-age-hours` rule: `export_prices.py` writes about three years of daily closes per holding into `data/prices.json`, **converted to EUR** — the returns a euro investor actually lives through, currency moves included. The Correlation page builds everything from it.

| Source | Covers |
|---|---|
| `api.onvista.de` | every DEGIRO product, looked up by ISIN; US stocks price off their home listing in USD, Tradegate products off Xetra |
| Coinbase daily candles | crypto, quoted straight in EUR |
| ECB reference rates (`api.frankfurter.dev`) | the FX conversion of non-EUR closes |

The ISIN comes from `positions.json` or, for exports from before that field, from the trades in `taxes.json`. A series that fails to download keeps the previous run's copy. Yahoo is deliberately not used: its keyless chart API answers 429 more often than not.

```bash
.venv/bin/python export_prices.py
```

## Portfolio history

`history.py` is not run directly — the broker exporters call `record_snapshot()`, which upserts
one entry per local date into `data/history.json` from whichever of `positions.json` and
`revolut.json` exist. Re-running an exporter the same day refreshes that day's numbers rather
than appending a duplicate; the portfolio-value chart on Holdings reads the result.

## Web app

`web/` is the Plout dashboard — Vite + React + TypeScript + Tailwind, in a single muted
palette (dyed-cloth canvas, near-white cards, sea-green gains, madder losses — there is no dark theme),
in English / Català / Español. It reads `data/` live (Vite's `publicDir` points at it, so no
data is copied or committed), which means every page degrades gracefully to an empty state until
you've run the exporter behind it.

| Page | Shows | Needs |
|---|---|---|
| **Dashboard** (`/`) | Net worth with today's move and the value chart, cash accounts, top holdings, this month's spending by category, Boosted interest, recent transactions, upcoming dividends | whatever exists — every card degrades to an empty state |
| **Holdings** (`/holdings`) | KPI cards, sector donut, portfolio-value chart, positions table | `export_degiro.py`, `export_revolut.py`, `history.py` |
| **Transactions** (`/transactions`) | Month navigator, spend/income KPIs, 12-month stacked bar chart, category donut and breakdown, searchable statement grouped by day, Boosted interest (daily chart, monthly effective rate) | `export_revolut.py` |
| **Correlation** (`/correlation`) | Diversification KPIs (portfolio volatility, diversification ratio, weighted correlation, independent bets), clustered correlation heatmap, market graph with a θ slider and its maximum independent set (the heaviest when several tie, after Hidaka et al., IEEE Access 2023), risk contribution per holding vs. its weight | `export_prices.py` plus the broker exports |
| **Dividends** | Net/gross totals, per-year table, upcoming and all payments | `export_dividends.py` |
| **Tax return** | Per-fiscal-year capital gains (FIFO), investment income, filing pack | `export_taxes.py` |

```bash
cd web
npm install    # Node 20 — see web/.nvmrc
npm run dev    # your real data from data/ (npm run demo for the synthetic one)
```

Then open the printed local URL in your browser. Never deploy `npm run build`'s `dist/`: it
bundles `data/`. Only `npm run build:demo` (→ `dist-demo/`) is meant to be published.
