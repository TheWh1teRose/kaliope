import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import { t } from '@/i18n'
import { seriesGraph } from '@/series/fixtures'

import PipelineCanvas from './PipelineCanvas.vue'

describe('pipeline canvas', () => {
  it('shows the planner and every episode with state and cost', () => {
    const wrapper = mount(PipelineCanvas, {
      props: { graph: seriesGraph(), live: { 'r2:script': 'Beat 4 von 7' }, waiting: { 3: 'wartet auf Folge 2' } },
    })
    expect(wrapper.findAll('[data-node]')).toHaveLength(3 + 3 * 4)
    const running = wrapper.get('[data-node="r2:script"]')
    expect(running.text()).toContain('Beat 4 von 7')
    expect(running.text()).toContain(t.graph.status.running)
    expect(wrapper.get('[data-node="r3:script"]').text()).toContain('wartet auf Folge 2')
    expect(wrapper.get('[data-node="r1:select"]').text()).toContain('$0.0100')
  })

  it('emits the node and the lane that were clicked', async () => {
    const wrapper = mount(PipelineCanvas, { props: { graph: seriesGraph() } })
    await wrapper.get('[data-node="r1:outline"]').trigger('click')
    await wrapper.get('[data-lane="3"]').trigger('click')
    expect(wrapper.emitted('select')?.[0]?.[0]).toMatchObject({ runId: 'r1', episode: 1 })
    expect(wrapper.emitted('lane')?.[0]).toEqual([3])
  })

  it('adds the inputs in the detailed view', async () => {
    const wrapper = mount(PipelineCanvas, { props: { graph: seriesGraph() } })
    expect(wrapper.get('[data-node="r2:script"]').text()).not.toContain('series_context')
    await wrapper.get('[data-mode="detail"]').trigger('click')
    expect(wrapper.get('[data-node="r2:script"]').text()).toContain('series_context')
  })
})
