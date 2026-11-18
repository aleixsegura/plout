import { useTranslation } from "./i18n/LanguageContext";
import { AnalysisBody, AnalysisDisclaimer } from "./AnalysisView";
import { analysisAvailable, useAnalysisStream } from "./useAnalysisStream";

/** "Analyse my portfolio composition": one streamed Claude answer over the
 * exports, held in component state only — reload and it is gone. */
export default function AnalysisPage() {
  const { t, lang } = useTranslation();
  const { state, start, stop } = useAnalysisStream("/__analysis/portfolio");
  const streaming = state.status === "streaming";

  return (
    <>
      <h1 className="mb-2 font-display text-[26px] font-semibold tracking-tight">{t.analysis.title}</h1>
      <p className="mb-5 max-w-[68ch] text-[14px] text-secondary">{t.analysis.intro}</p>

      {!analysisAvailable ? (
        <p className="text-[13px] text-muted">{t.analysis.unavailable}</p>
      ) : (
        <div className="flex max-w-[78ch] flex-col gap-4">
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => (streaming ? stop() : start({ lang }))}
              className="rounded-md bg-accent px-3.5 py-2 text-[13.5px] font-semibold text-canvas hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            >
              {streaming ? t.analysis.stop : state.status === "idle" ? t.analysis.analyse : t.analysis.again}
            </button>
            <span className="text-[12px] text-muted">{t.analysis.ephemeral}</span>
          </div>
          <AnalysisDisclaimer />
          {state.status !== "idle" && (
            <div className="card px-5 py-4">
              <AnalysisBody state={state} />
            </div>
          )}
        </div>
      )}
    </>
  );
}
