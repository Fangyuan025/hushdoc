/**
 * Top-level React error boundary.
 *
 * Without one, a runtime exception in render unmounts the whole React
 * tree and the user sees a blank page that LOOKS like the app froze.
 * Worse, useEffect cleanups don't run during a render crash, so any
 * timers / fetches the unmounted tree set up keep ticking, which
 * compounds into the "browser feels stuck" symptom even though the
 * underlying JS is technically responsive.
 *
 * This boundary catches everything below <App/>, prints a quiet
 * recovery panel, and offers a one-click reload. The error is also
 * logged to the console so the user can copy a stack trace into an
 * issue.
 */
import { Component, type ErrorInfo, type ReactNode } from "react"

import { detectLang, translate } from "@/lib/i18n"

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary] caught:", error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    // ErrorBoundary is a class component so it can't use ``useT()``.
    // Resolve the language directly from localStorage / navigator.
    // The boundary's own state has no React rerender hook into our
    // Provider, so the user's choice from Settings is the next best
    // thing if it's already been persisted.
    const lang = detectLang()
    const t = (k: Parameters<typeof translate>[1]) => translate(lang, k)
    return (
      <div className="flex h-full min-h-0 items-center justify-center p-6">
        <div className="w-full max-w-lg space-y-4 rounded-lg border border-destructive/30 bg-destructive/5 p-5 text-sm">
          <div className="text-base font-semibold text-destructive">
            {t("error.title")}
          </div>
          <p className="text-muted-foreground">{t("error.body")}</p>
          <pre className="max-h-48 overflow-auto rounded border bg-card p-2 font-mono text-[11px] text-muted-foreground">
            {error.name}: {error.message}
          </pre>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => this.setState({ error: null })}
              className="rounded border bg-card px-3 py-1.5 text-xs hover:bg-accent"
            >
              {t("error.recover")}
            </button>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded border bg-card px-3 py-1.5 text-xs hover:bg-accent"
            >
              {t("error.reload")}
            </button>
          </div>
        </div>
      </div>
    )
  }
}
