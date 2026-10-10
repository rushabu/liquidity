import { useCallback, useState } from "react";

/** UI state that survives switching tabs and leaving the page (kept in memory until the browser tab is
 *  reloaded), so filters, selected bucket and scenario stay where the user left them. */
const store = new Map<string, unknown>();

export function useSticky<T>(key: string, initial: T): [T, (v: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => (store.has(key) ? (store.get(key) as T) : initial));
  const set = useCallback((v: T | ((prev: T) => T)) => {
    setValue((prev) => {
      const next = typeof v === "function" ? (v as (p: T) => T)(prev) : v;
      store.set(key, next);
      return next;
    });
  }, [key]);
  return [value, set];
}

export function clearSticky(): void {
  store.clear();
}
