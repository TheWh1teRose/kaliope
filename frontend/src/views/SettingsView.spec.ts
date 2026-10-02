import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

import SettingsView from './SettingsView.vue'

vi.mock('@/components/settings/ProfileSettings.vue', () => ({
  default: { name: 'ProfileSettings', template: '<section class="profile-stub" />' },
}))
vi.mock('@/components/settings/MembersSettings.vue', () => ({
  default: { name: 'MembersSettings', template: '<section class="members-stub" />' },
}))

async function open(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/settings', name: 'settings', component: SettingsView }],
  })
  await router.push(path)
  const wrapper = mount(SettingsView, { global: { plugins: [router] } })
  await flushPromises()
  return { router, wrapper }
}

function tab(wrapper: ReturnType<typeof mount>, label: string) {
  const button = wrapper.findAll('[role="tab"]').find((item) => item.text() === label)
  if (!button) throw new Error(`missing tab ${label}`)
  return button
}

describe('settings tabs', () => {
  it('opens the profile by default', async () => {
    const { wrapper } = await open('/settings')
    expect(tab(wrapper, 'Profil').attributes('aria-selected')).toBe('true')
    expect(wrapper.find('.profile-stub').exists()).toBe(true)
  })

  it('opens the tab named in the address', async () => {
    const { wrapper } = await open('/settings?tab=organisation')
    expect(tab(wrapper, 'Organisation').attributes('aria-selected')).toBe('true')
    expect(wrapper.find('.members-stub').exists()).toBe(true)
  })

  it('writes the chosen tab into the address and shows the export there', async () => {
    const { router, wrapper } = await open('/settings')
    await tab(wrapper, 'Export').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/settings?tab=export')
    expect(wrapper.find('a[href="/api/exports/edit-events.jsonl"]').exists()).toBe(true)
  })

  it('moves between tabs with the arrow keys', async () => {
    const { router, wrapper } = await open('/settings')
    await wrapper.find('[role="tablist"]').trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(router.currentRoute.value.query.tab).toBe('organisation')
  })
})
