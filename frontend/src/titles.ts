/** The title a run is shown under: its name, or the document title it had before names existed. */
export function runTitle(
  run: {
    name?: string | null
    document_title?: string | null
    document_id?: string
  } | null | undefined,
  fallback = '',
): string {
  if (!run) return fallback
  const name = run.name?.trim()
  if (name) return name
  return run.document_title || run.document_id || fallback
}

/** The title a series is shown under: its name, or the plan title, or the document title. */
export function seriesTitle(
  series: {
    name?: string | null
    document_title?: string | null
    document_id?: string
    plan?: { title?: string | null } | null
  } | null | undefined,
  fallback = '',
): string {
  if (!series) return fallback
  const name = series.name?.trim()
  if (name) return name
  return series.plan?.title || series.document_title || series.document_id || fallback
}

/** A loaded run in an experiment: its name, or the document title stored with the source. */
export function sourceTitle(
  source: { name?: string | null; document_title?: string | null } | null | undefined,
): string {
  if (!source) return ''
  const name = source.name?.trim()
  return name || source.document_title || ''
}
