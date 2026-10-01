import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

import ProfileSettings from './ProfileSettings.vue'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn() }))

vi.mock('@/api/client', async (original) => ({
  ...(await original<typeof import('@/api/client')>()),
  api,
}))

const me = { id: 'me', email: 'me@kalliope.test', name: 'Ich', role: 'admin', active: true }

beforeEach(() => {
  setActivePinia(createPinia())
  useAuthStore().user = { ...me } as never
  api.post.mockReset()
  api.patch.mockReset()
})

describe('profile', () => {
  it('saves name and email and confirms it', async () => {
    api.patch.mockResolvedValue({ ...me, name: 'Neu', email: 'neu@kalliope.test' })
    const wrapper = mount(ProfileSettings)
    await wrapper.find('#profile-name').setValue('Neu')
    await wrapper.find('#profile-email').setValue('neu@kalliope.test')
    await wrapper.find('form[aria-labelledby="profile-title"]').trigger('submit')
    await flushPromises()
    expect(api.patch).toHaveBeenCalledWith('/api/account', {
      name: 'Neu',
      email: 'neu@kalliope.test',
    })
    expect(useAuthStore().user?.name).toBe('Neu')
    expect(wrapper.find('[role="status"]').text()).toBe('Profil gespeichert.')
  })

  it('does not send a password change when the repetition differs', async () => {
    const wrapper = mount(ProfileSettings)
    await wrapper.find('#password-current').setValue('old-password-1')
    await wrapper.find('#password-new').setValue('new-password-1')
    await wrapper.find('#password-repeat').setValue('new-password-2')
    await wrapper.find('form[aria-labelledby="password-title"]').trigger('submit')
    await flushPromises()
    expect(api.post).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('stimmen nicht überein')
  })

  it('reports a wrong current password and clears the fields on success', async () => {
    api.post.mockRejectedValueOnce(new ApiError(403, 'Wrong password', 'incorrect'))
    const wrapper = mount(ProfileSettings)
    const form = wrapper.find('form[aria-labelledby="password-title"]')
    await wrapper.find('#password-current').setValue('old-password-1')
    await wrapper.find('#password-new').setValue('new-password-1')
    await wrapper.find('#password-repeat').setValue('new-password-1')
    await form.trigger('submit')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toBe('Das aktuelle Passwort ist falsch.')

    api.post.mockResolvedValueOnce(undefined)
    await form.trigger('submit')
    await flushPromises()
    expect(api.post).toHaveBeenLastCalledWith('/api/account/password', {
      current_password: 'old-password-1',
      new_password: 'new-password-1',
    })
    expect((wrapper.find('#password-new').element as HTMLInputElement).value).toBe('')
    expect(wrapper.find('[role="status"]').text()).toContain('Passwort geändert')
  })
})
