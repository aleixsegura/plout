import { useEffect, useRef } from "react";
import { useTranslation } from "./i18n/LanguageContext";
import { AnalysisBody, AnalysisDisclaimer } from "./AnalysisView";
import { useAnalysisStream } from "./useAnalysisStream";
import type { Stock } from "./types";

/** Side drawer with a streamed read on one asset. Opening it is the only thing
 * that calls the API; closing it aborts the request and drops the text, so
 * re-opening the same row asks again. */
export default function AssetAnalysisPanel({ stock, onClose }: { stock: Stock; onClose: () => void }) {
  const { t, lang } = useTranslation();
  const { state, start } = useAnalysisStream("/__analysis/asset");
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    void start({ symbol: stock.symbol, lang });
    // The language at opening time is the one the answer comes back in, so
    // `lang` is deliberately not a dependency.
  }, [stock.symbol, start]);

  useEffect(() => {
    closeButton.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/20" onClick={onClose} aria-hidden="true" />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={t.analysis.analyseRow(stock.name)}
        className="fixed inset-y-0 right-0 z-50 flex w-[min(560px,100vw)] flex-col border-l border-hairline-strong bg-canvas shadow-xl"
      >
        <header className="flex items-start gap-3 border-b border-hairline px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate font-display text-[19px] font-semibold tracking-tight text-primary">{stock.name}</h2>
            <p className="text-xs text-muted">{stock.symbol}</p>
          </div>
          <button
            ref={closeButton}
            type="button"
            onClick={onClose}
            aria-label={t.analysis.close}
            title={t.analysis.close}
            className="ml-auto rounded-md p-1.5 text-muted hover:bg-hover hover:text-primary focus-visible:outline-2 focus-visible:outline-focus"
          >
            <svg viewBox="0 0 24 24" width={16} height={16} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </header>
        <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-5 py-4">
          <AnalysisDisclaimer />
          <AnalysisBody state={state} />
        </div>
      </aside>
    </>
  );
}
