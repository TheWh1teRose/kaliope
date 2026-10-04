import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { NodeParam } from '@/api/types'
import ParamField from './ParamField.vue'

const param: NodeParam = {
  key: 'max_tokens', label: 'Max output tokens per beat', type: 'int',
  default: 8000, minimum: 0, maximum: 64000, advanced: true,
  options: [], description: null,
}

function render(value?: number) {
  return mount(ParamField, { props: { param, value, models: [] } })
}

describe('script output cap', () => {
  it('disables the cap explicitly and restores the existing numeric value', async () => {
    const wrapper = render(64000)
    const toggle = wrapper.get('[data-disable-cap]')
    await toggle.setValue(true)
    expect(wrapper.emitted('update')).toEqual([[0]])
    await wrapper.setProps({ value: 0 })
    expect(wrapper.get('input[type="number"]').attributes('disabled')).toBeDefined()
    await toggle.setValue(false)
    expect(wrapper.emitted('update')).toEqual([[0], [64000]])
  })

  it('loads a saved disabled cap and can restore the node default', async () => {
    const wrapper = render(0)
    expect((wrapper.get('[data-disable-cap]').element as HTMLInputElement).checked).toBe(true)
    await wrapper.get('[data-disable-cap]').setValue(false)
    expect(wrapper.emitted('update')).toEqual([[8000]])
  })

  it('keeps unset settings implicit and reset distinct from disabling', async () => {
    const wrapper = render()
    expect(wrapper.emitted('update')).toBeUndefined()
    expect(wrapper.get('input[type="number"]').attributes('placeholder')).toBe('8000')
    await wrapper.get('[data-disable-cap]').setValue(true)
    await wrapper.setProps({ value: 0 })
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('update')).toEqual([[0], [null]])
  })

  it('keeps ordinary numeric inputs working', async () => {
    const wrapper = render(64000)
    await wrapper.get('input[type="number"]').setValue(32000)
    expect(wrapper.emitted('update')).toEqual([[32000]])
    await wrapper.setProps({ param: { ...param, minimum: 1000 } })
    expect(wrapper.find('[data-disable-cap]').exists()).toBe(false)
  })
})
