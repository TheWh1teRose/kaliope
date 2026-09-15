import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { IngestionReport } from '@/api/types'
import ConfidenceStrip from '@/components/ConfidenceStrip.vue'

function report(overrides: Partial<IngestionReport> = {}): IngestionReport {
  return {
    language: 'de',
    language_confidence: 0.9,
    page_count: 10,
    extractable_words: 3000,
    narratable_words: 2500,
    visual_content_ratio: 0.1,
    text_density: 300,
    structure_source: 'outline',
    structure_confidence: 'high',
    section_count: 8,
    zone_distribution: { body: 40 },
    zone_uncertain_ratio: 0.05,
    table_count: 1,
    boilerplate_lines_removed: 12,
    anchor_integrity: 1,
    reading_order_confidence: 1,
    warnings: [],
    ingestion_confidence: 'high',
    confidence_reasons: [],
    ...overrides,
  }
}

describe('ConfidenceStrip', () => {
  it('shows one cell per measured property', () => {
    const wrapper = mount(ConfidenceStrip, { props: { report: report() } })
    expect(wrapper.findAll('.strip__cell')).toHaveLength(5)
  })

  it('marks a good parse as passing across the board', () => {
    const wrapper = mount(ConfidenceStrip, { props: { report: report() } })
    expect(wrapper.findAll('.strip__cell--pass')).toHaveLength(5)
    expect(wrapper.findAll('.strip__cell--fail')).toHaveLength(0)
  })

  it('names the measurement that failed rather than only the overall level', () => {
    const wrapper = mount(ConfidenceStrip, {
      props: { report: report({ anchor_integrity: 0.8, text_density: 20 }) },
    })
    const failing = wrapper.findAll('.strip__cell--fail')
    expect(failing).toHaveLength(2)
    expect(wrapper.text()).toContain('80%')
    expect(wrapper.text()).toContain('20')
  })

  it('treats a flat structure as a failed band, not a missing one', () => {
    const wrapper = mount(ConfidenceStrip, {
      props: { report: report({ structure_source: 'flat', structure_confidence: 'low' }) },
    })
    expect(wrapper.find('.strip__cell--fail').text()).toContain('flach')
  })
})
