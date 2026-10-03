import { describe, expect, it } from 'vitest'

import { sourceLabel } from '@/experiments/outputMeta'

import { runTitle, seriesTitle, sourceTitle } from './titles'

describe('run and series titles', () => {
  it('uses a name and falls back to the document title', () => {
    expect(runTitle({ name: ' Kurzfassung ', document_title: 'Klima', document_id: 'd1' })).toBe(
      'Kurzfassung',
    )
    expect(runTitle({ name: '  ', document_title: 'Klima', document_id: 'd1' })).toBe('Klima')
    expect(runTitle({ name: null, document_title: 'Klima', document_id: 'd1' })).toBe('Klima')
    expect(runTitle({ document_id: 'd1' }, 'Lauf')).toBe('d1')
    expect(runTitle(null, 'Lauf')).toBe('Lauf')
  })

  it('uses a series name and otherwise the plan title', () => {
    const series = {
      name: null as string | null,
      document_title: 'Klima',
      document_id: 'd1',
      plan: { title: 'Klimawandel verstehen' },
    }
    expect(seriesTitle(series)).toBe('Klimawandel verstehen')
    expect(seriesTitle({ ...series, name: 'Klimaserie' })).toBe('Klimaserie')
    expect(seriesTitle({ ...series, plan: null })).toBe('Klima')
    expect(seriesTitle(null, 'Serie')).toBe('Serie')
  })

  it('prefers the run name in a loaded source and in the Sammlung', () => {
    expect(sourceTitle({ name: 'Kurzfassung', document_title: 'Klima' })).toBe('Kurzfassung')
    expect(sourceTitle({ document_title: 'Klima' })).toBe('Klima')
    expect(sourceLabel({ name: 'Kurzfassung', document_title: 'Klima', beat_title: 'Licht' })).toBe(
      'Kurzfassung · Licht',
    )
    expect(sourceLabel({ document_title: 'Klima', beat_title: 'Licht' })).toBe('Klima · Licht')
  })
})
