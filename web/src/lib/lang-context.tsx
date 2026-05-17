/**
 * React glue for the i18n layer.
 *
 *   <LangProvider>           ← wraps the app in main.tsx
 *     <App />
 *   </LangProvider>
 *
 *   const { lang, setLang } = useLang()   ← read / change current
 *   const t = useT()                       ← translate keys
 *   t("settings.title")                    ← "Settings" / "设置"
 *   t("header.ready", { n: 42 })          ← interpolation
 *
 * State lives in component state; ``setLang`` persists to
 * localStorage so the choice survives reloads. The Provider also
 * mirrors the current lang onto ``document.documentElement.lang`` so
 * screen readers + browser spellcheck pick the right language.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react"
import type { ReactNode } from "react"

import { type Dict, type Lang, detectLang, translate } from "@/lib/i18n"

interface LangContextValue {
  lang: Lang
  setLang: (l: Lang) => void
}

const LangContext = createContext<LangContextValue | null>(null)

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => detectLang())

  useEffect(() => {
    try {
      document.documentElement.lang = lang === "zh" ? "zh-CN" : "en"
    } catch {
      /* SSR safety */
    }
  }, [lang])

  const setLang = useCallback((next: Lang) => {
    setLangState(next)
    try {
      localStorage.setItem("hushdoc-lang", next)
    } catch {
      /* ignore quota / storage-blocked */
    }
  }, [])

  const value = useMemo(() => ({ lang, setLang }), [lang, setLang])
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>
}

export function useLang(): LangContextValue {
  const ctx = useContext(LangContext)
  if (!ctx) {
    // Provider not mounted -- happens in isolated component tests.
    // Return a no-op fallback so the hook doesn't crash; tests can
    // assert against the English fallback.
    return {
      lang: "en",
      setLang: () => {},
    }
  }
  return ctx
}

/** Returns a translator bound to the current language. The fn is
 *  re-created when ``lang`` flips so consumers re-render. */
export function useT() {
  const { lang } = useLang()
  return useCallback(
    (key: keyof Dict, vars?: Record<string, string | number>) =>
      translate(lang, key, vars),
    [lang],
  )
}
