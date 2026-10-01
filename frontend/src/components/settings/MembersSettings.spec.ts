import { type DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import type { Member } from '@/api/types'

import MembersSettings from './MembersSettings.vue'

const members: Member[] = [
  { id: 'me', email: 'me@kalliope.test', name: 'Ich', created_at: '2026-01-02T00:00:00Z', is_self: true },
  { id: 'u2', email: 'two@kalliope.test', name: 'Zwei', created_at: '2026-02-03T00:00:00Z', is_self: false },
]

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/api/client', async (original) => ({
  ...(await original<typeof import('@/api/client')>()),
  api,
}))

beforeEach(() => {
  setActivePinia(createPinia())
  api.get.mockReset().mockResolvedValue(members.map((m) => ({ ...m })))
  api.post.mockReset()
  api.put.mockReset().mockResolvedValue(undefined)
  api.delete.mockReset().mockResolvedValue(undefined)
})

async function mountMembers() {
  const wrapper = mount(MembersSettings, { attachTo: document.body })
  await flushPromises()
  return wrapper
}

function button(
  wrapper: ReturnType<typeof mount>,
  label: string,
  scope: Pick<DOMWrapper<Element>, 'findAll'> = wrapper,
) {
  const found = scope.findAll('button').find((item) => item.text() === label)
  if (!found) throw new Error(`missing button ${label}`)
  return found
}

describe('members', () => {
  it('lists members and offers no actions on yourself', async () => {
    const wrapper = await mountMembers()
    const rows = wrapper.findAll('li.item')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('Sie')
    expect(rows[0].text()).not.toContain('Entfernen')
    expect(rows[1].text()).toContain('Entfernen')
    wrapper.unmount()
  })

  it('creates a member with a generated password and shows it once', async () => {
    api.post.mockImplementation(async (_path: string, body: { email: string; name: string }) => ({
      id: 'u3',
      email: body.email.trim().toLowerCase(),
      name: body.name,
      created_at: '2026-03-01T00:00:00Z',
      is_self: false,
    }))
    const wrapper = await mountMembers()
    await wrapper.find('#member-name').setValue('Neu')
    await wrapper.find('#member-email').setValue('neu@kalliope.test')
    await button(wrapper, 'Passwort erzeugen').trigger('click')
    const generated = (wrapper.find('#member-password').element as HTMLInputElement).value
    expect(generated.length).toBeGreaterThanOrEqual(10)
    expect(wrapper.find('#member-password').attributes('type')).toBe('text')

    await wrapper.find('form[aria-labelledby="add-title"]').trigger('submit')
    await flushPromises()

    expect(api.post).toHaveBeenCalledWith('/api/users', {
      name: 'Neu',
      email: 'neu@kalliope.test',
      password: generated,
    })
    expect(wrapper.find('.handover').text()).toContain(generated)
    expect((wrapper.find('#member-password').element as HTMLInputElement).value).toBe('')
    expect(wrapper.findAll('li.item')).toHaveLength(3)

    await button(wrapper, 'Ausblenden').trigger('click')
    expect(wrapper.text()).not.toContain(generated)
    wrapper.unmount()
  })

  it('shows a refusal in German', async () => {
    api.post.mockRejectedValue(new ApiError(409, 'Email in use', 'Another account uses it.'))
    const wrapper = await mountMembers()
    await wrapper.find('#member-name').setValue('Neu')
    await wrapper.find('#member-email').setValue('two@kalliope.test')
    await wrapper.find('#member-password').setValue('long-enough-pw')
    await wrapper.find('form[aria-labelledby="add-title"]').trigger('submit')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toBe(
      'Diese E-Mail-Adresse wird bereits verwendet.',
    )
    wrapper.unmount()
  })

  it('removes a member only after the in-page confirmation', async () => {
    const wrapper = await mountMembers()
    const row = wrapper.findAll('li.item')[1]
    await button(wrapper, 'Entfernen', row).trigger('click')
    await flushPromises()
    expect(api.delete).not.toHaveBeenCalled()
    const confirm = wrapper.find('.confirm')
    expect(confirm.exists()).toBe(true)
    expect(document.activeElement?.id).toBe('remove-u2')

    await button(wrapper, 'Ja, entfernen', confirm).trigger('click')
    await flushPromises()
    expect(api.delete).toHaveBeenCalledWith('/api/users/u2')
    expect(wrapper.findAll('li.item')).toHaveLength(1)
    expect(wrapper.find('[role="status"]').text()).toBe('Mitglied entfernt.')
    wrapper.unmount()
  })

  it('sets a new password for another member', async () => {
    const wrapper = await mountMembers()
    const row = wrapper.findAll('li.item')[1]
    await button(wrapper, 'Neues Passwort setzen', row).trigger('click')
    await flushPromises()
    await wrapper.find('#reset-u2').setValue('another-password')
    await wrapper.find('li.item form').trigger('submit')
    await flushPromises()
    expect(api.put).toHaveBeenCalledWith('/api/users/u2/password', {
      password: 'another-password',
    })
    expect(wrapper.find('.handover').text()).toContain('another-password')
    wrapper.unmount()
  })
})
