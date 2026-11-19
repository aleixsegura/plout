// Dev-server endpoints that ask Claude about the portfolio and stream the
// answer back to the page as Server-Sent Events:
//
//   POST /__analysis/portfolio  { lang }          → composition analysis (#1)
//   POST /__analysis/asset      { symbol, lang }  → one holding / watchlist row (#2)
//
// Each request runs the local Claude Code CLI (`claude -p`, see claudeCli.ts),
// so it counts against the logged-in Claude plan and needs no API key. Nothing
// is persisted: the exports are read per request, the CLI runs with
// --no-session-persistence, and the answer goes straight to the response.

import type { IncomingMessage, ServerResponse } from "node:http";
import type { Plugin } from "vite";
import { runClaudeAnalysis, type AnalysisEvent } from "./claudeCli";
import { assetContext, loadExports, portfolioContext } from "./portfolioContext";

const LANGUAGE_NAMES = { en: "English", ca: "Catalan", es: "Spanish" } as const;
type Lang = keyof typeof LANGUAGE_NAMES;

const COMMON_RULES = `
Write for the portfolio's owner: a retail investor in Spain who knows their own holdings and wants a frank, specific read, not a generic primer.

The JSON in the user message was computed from the owner's broker exports. Treat its figures (weights, HHI, P/L, yields, returns) as exact and authoritative; quote them rather than recomputing them. Use web search for anything current that the exports cannot hold — recent results, news, ETF holdings and fees, business descriptions — and say when a figure comes from the web rather than the exports.

This is analysis, not advice: do not tell the owner to buy, sell or hold anything, and do not add a disclaimer — the page shows one.

Format: GitHub-flavoured Markdown using only ## headings, short paragraphs, "-" bullet lists and **bold**. No tables, no code blocks, no title heading at the top. Open with the substance, not a preamble about what you are going to do.`;

const PORTFOLIO_SYSTEM = `You analyse the composition of a personal investment portfolio (DEGIRO stocks and ETFs, Revolut crypto and cash).
${COMMON_RULES}

Cover, under a ## heading each:
- Concentration: top-N weights, HHI and the effective number of holdings; which single positions and which sectors carry the portfolio.
- Sectors: weights from the data; treat the "Other" bucket as unclassified holdings (a data-quality gap), not as a sector.
- Currency and geography: real exposure once ETFs are looked through — an ETF trading in EUR can be mostly US assets. Look up what each ETF holds.
- Overlap: single names that are also large constituents of the ETFs held, so the same megacaps arrive twice.
- Cash: share of the total and how it is split; whether it reads as drag or dry powder.
- Crypto: weight in the total and how concentrated the crypto sleeve is.
- Income and costs: dividend yield on cost and fee drag, with the window the figures cover.
- What would change the picture: the two or three concrete risks this specific composition carries.

Aim for 700–1000 words.`;

const ASSET_SYSTEM = `You analyse one asset from a personal investment portfolio or watchlist.
${COMMON_RULES}

Cover, under a ## heading each:
- What it is: the business (or fund, or protocol) in two lines, sector, market cap and where it lists — enough that a ticker bought a year ago is identifiable.
- How it is doing: recent price action (the exports carry 1m/3m/1y/3y returns in EUR) and what is current — last results, guidance, news — from web search.
- In this portfolio: weight, rank, P/L against the average buy price, its share of its sector, dividends received. Omit this section entirely when "position" is null (a watchlist name that is not held).

Adapt to the asset type:
- Crypto has no earnings: cover supply, narrative and where it sits against the rest of the crypto sleeve instead of results.
- ETFs: cover top holdings, ongoing charges (expense ratio) and the index tracked, and overlap with the single names held elsewhere ("otherSingleNamesHeld"). Market cap does not apply.

Aim for 350–550 words.`;

function send(res: ServerResponse, event: AnalysisEvent) {
  res.write(`data: ${JSON.stringify(event)}\n\n`);
}

async function readBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 10_000) throw new Error("request body too large");
    chunks.push(chunk as Buffer);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  return text ? (JSON.parse(text) as Record<string, unknown>) : {};
}

const asLang = (v: unknown): Lang => (v === "ca" || v === "es" ? v : "en");

export function analysisPlugin(): Plugin {
  return {
    name: "plout:analysis",
    apply: "serve",
    configureServer(server) {
      const dataDir = server.config.publicDir;

      server.middlewares.use(async (req, res, next) => {
        const url = req.url?.split("?")[0];
        if (url !== "/__analysis/portfolio" && url !== "/__analysis/asset") return next();
        if (req.method !== "POST") {
          res.statusCode = 405;
          res.end();
          return;
        }

        // Closing the panel aborts the fetch; kill the CLI so it stops using
        // the plan for an answer nobody reads. Registered first, so a request
        // dropped while its body is still being read never starts it at all.
        const abort = new AbortController();
        res.on("close", () => abort.abort());

        let body: Record<string, unknown>;
        try {
          body = await readBody(req);
        } catch {
          res.statusCode = 400;
          res.end("invalid JSON body");
          return;
        }
        const lang = asLang(body.lang);
        const exports = loadExports(dataDir);

        let system: string;
        let context: unknown;
        if (url === "/__analysis/portfolio") {
          if (!exports.positions && !exports.revolut) {
            res.statusCode = 404;
            res.end("no positions.json or revolut.json to analyse");
            return;
          }
          system = PORTFOLIO_SYSTEM;
          context = portfolioContext(exports);
        } else {
          const symbol = typeof body.symbol === "string" ? body.symbol.trim() : "";
          if (!symbol || symbol.length > 32) {
            res.statusCode = 400;
            res.end("missing symbol");
            return;
          }
          context = assetContext(exports, symbol);
          if (!context) {
            res.statusCode = 404;
            res.end(`no holding or watchlist row for ${symbol}`);
            return;
          }
          system = ASSET_SYSTEM;
        }

        const today = new Date().toISOString().slice(0, 10);
        const userContent =
          `Today is ${today}. Answer in ${LANGUAGE_NAMES[lang]}.\n\n` +
          `<exports>\n${JSON.stringify(context)}\n</exports>`;

        if (abort.signal.aborted) return;
        res.writeHead(200, {
          "Content-Type": "text/event-stream; charset=utf-8",
          "Cache-Control": "no-cache, no-transform",
          Connection: "keep-alive",
        });

        try {
          await runClaudeAnalysis(system, userContent, abort.signal, (event) => {
            if (event.type === "error") server.config.logger.warn(`  analysis: ${event.message}`);
            send(res, event);
          });
        } finally {
          res.end();
        }
      });
    },
  };
}
