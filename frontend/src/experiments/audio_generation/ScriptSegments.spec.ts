import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'

import ScriptSegments from './ScriptSegments.vue'
import { emptySetup, type Line } from './synthesis'

const lines: Line[] = Array.from({ length: 6 }, (_, i) => ({
  segment_id: `s${i + 1}`,
  speaker: i % 2 ? 'Gast' : 'Host',
  tagged: `Zeile ${i + 1}. ${'x'.repeat(i === 5 ? 400 : 10)}`,
}))

it('selects all, none, the first minute and a shift-clicked stretch', async () => {
  const wrapper = mount(ScriptSegments, { props: { lines, setup: { ...emptySetup(), segment_ids: [] } } })
  await wrapper.find('[data-select="all"]').trigger('click')
  await wrapper.find('[data-select="none"]').trigger('click')
  await wrapper.find('[data-select="minute"]').trigger('click')
  const boxes = wrapper.findAll('input[type="checkbox"]')
  await boxes[1]!.trigger('click')
  await boxes[3]!.trigger('click', { shiftKey: true })
  expect(wrapper.emitted('select')).toEqual([
    [null],
    [[]],
    // About a minute is more than these short lines, so the first minute is all of them.
    [null],
    [['s2']],
    [['s2', 's3', 's4']],
  ])
})

it('shows long segments clamped until opened, and edits by segment', async () => {
  const wrapper = mount(ScriptSegments, { props: { lines, setup: emptySetup() } })
  const long = wrapper.find('[data-segment="s6"]')
  expect(long.find('.seg__text--clamped').exists()).toBe(true)
  await long.find('.more').trigger('click')
  expect(wrapper.find('[data-segment="s6"] .seg__text--clamped').exists()).toBe(false)

  await wrapper.find('[data-segment="s1"] [data-edit-start]').trigger('click')
  await wrapper.find('[data-segment="s1"] textarea').setValue('Neu.')
  await wrapper.find('[data-segment="s1"] textarea').setValue(lines[0]!.tagged)
  expect(wrapper.emitted('edit')).toEqual([
    ['s1', 'Neu.'],
    ['s1', null],
  ])
})
