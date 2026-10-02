import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import type { BenchValueOut } from '@/api/types'

import { loadArtifactMode, setArtifactMode } from './artifactView'
import BenchValueCard from './BenchValueCard.vue'

function value(overrides: Partial<BenchValueOut> = {}): BenchValueOut {
  return {
    key: 'outline',
    model: 'Outline',
    produced_by: 'outline',
    artifact_hash: null,
    summary: {},
    preview: '{"beats":[]}',
    truncated: false,
    available: true,
    source: 'edited',
    source_id: null,
    payload: { beats: [] },
    ...overrides,
  }
}

describe('BenchValueCard text and JSON', () => {
  beforeEach(() => {
    localStorage.clear()
    loadArtifactMode()
  })

  it('still emits edit from the JSON textarea', async () => {
    setArtifactMode('json')
    const wrapper = mount(BenchValueCard, {
      props: {
        value: value(),
        text: '{"beats":[]}',
        dirty: false,
        jsonError: '',
        open: true,
      },
    })
    const area = wrapper.get('textarea')
    await area.setValue('{"beats":[{"id":"b1"}]}')
    expect(wrapper.emitted('edit')?.[0]).toEqual(['{"beats":[{"id":"b1"}]}'])
  })

  it('has no textarea in text mode', () => {
    setArtifactMode('text')
    const wrapper = mount(BenchValueCard, {
      props: {
        value: value(),
        text: '{"beats":[]}',
        dirty: false,
        jsonError: '',
        open: true,
      },
    })
    expect(wrapper.find('textarea').exists()).toBe(false)
  })
})
