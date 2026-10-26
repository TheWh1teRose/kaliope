import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import LoadingState from '@/components/LoadingState.vue'
import { t } from '@/i18n'

describe('LoadingState', () => {
  it('announces the shared loading message with a spinner', () => {
    const wrapper = mount(LoadingState)
    expect(wrapper.attributes('role')).toBe('status')
    expect(wrapper.text()).toBe(t.common.loading)
    expect(wrapper.get('.loading__spinner').attributes('aria-hidden')).toBe('true')
  })

  it('reads a given label instead', () => {
    const wrapper = mount(LoadingState, { props: { label: t.sharing.loadingShare } })
    expect(wrapper.text()).toBe(t.sharing.loadingShare)
  })

  it('takes the spacing class of the spot it fills', () => {
    const wrapper = mount(LoadingState, { attrs: { class: 'pad' } })
    expect(wrapper.classes()).toEqual(expect.arrayContaining(['loading', 'pad']))
  })
})
