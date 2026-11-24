import { useTranslation } from "./i18n/LanguageContext";
import Markdown from "./Markdown";
import type { AnalysisState } from "./useAnalysisStream";

/** Disclaimer line every AI-written answer sits under. */
export function AnalysisDisclaimer() {
  const { t } = useTranslation();
  return (
    <p className="rounded-md bg-elevated px-3 py-2 text-[12.5px] leading-snug text-secondary" role="note">
      {t.analysis.disclaimer}
    </p>
  );
}

/** The streamed answer, its live activity line, and its error/usage footer. */
export function AnalysisBody({ state }: { state: AnalysisState }) {
  const { t } = useTranslation();
  const activity =
    state.activity === "web_search"
      ? t.analysis.searching
      : state.activity === "web_fetch"
        ? t.analysis.fetching
        : state.status === "streaming"
          ? t.analysis.thinking
          : null;

  return (
    <div aria-live="polite" aria-busy={state.status === "streaming"}>
      {state.text && <Markdown source={state.text} />}
      {activity && (
        <p className="mt-3 flex items-center gap-2 text-[12.5px] text-muted">
          <span className="inline-block size-1.5 animate-pulse rounded-full bg-accent" aria-hidden="true" />
          {activity}
        </p>
      )}
      {state.error && <p className="mt-3 text-[13px] text-loss">{t.analysis.error(state.error)}</p>}
      {state.status === "stopped" && <p className="mt-3 text-[12.5px] text-muted">{t.analysis.stopped}</p>}
      {state.usage && state.status === "done" && (
        <p className="mt-4 text-[11px] tabular-nums text-muted">
          {t.analysis.usage(state.usage.model, state.usage.inputTokens, state.usage.outputTokens)}
        </p>
      )}
    </div>
  );
}
