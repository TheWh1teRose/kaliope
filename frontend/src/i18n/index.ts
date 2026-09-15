import de from './de'

export type Messages = typeof de

/**
 * The console ships one locale. `t` exists so components never inline a
 * user-visible string, which is what §1.4 requires and what makes adding a
 * second locale a data change rather than a refactor.
 */
export const t: Messages = de

export function tt(path: string, fallback = ''): string {
  const value = path
    .split('.')
    .reduce<unknown>((acc, key) => (acc as Record<string, unknown> | undefined)?.[key], t)
  return typeof value === 'string' ? value : fallback || path
}
