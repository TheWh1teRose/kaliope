import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { sharingApi } from '@/api/sharing'
import { ApiError } from '@/api/client'
import ReviewLinkButton from './ReviewLinkButton.vue'

vi.mock('@/api/sharing', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sharing')>()),
  sharingApi: {
    options: vi.fn(),
    links: vi.fn(),
    preview: vi.fn(),
    create: vi.fn(),
    revoke: vi.fn(),
  },
}))
const metadata = {
  id: 'link-one',
  created_at: '2026-10-04T12:00:00Z',
  expires_at: '2026-11-03T12:00:00Z',
  status: 'active' as const,
}
const options = {
  title: 'Series',
  episodes: [
    {
      index: 1,
      title: 'One',
      takes: [
        {
          id: 'take-one',
          created_at: metadata.created_at,
          duration_s: 12,
          older_script: true,
        },
      ],
    },
    { index: 2, title: 'Two', takes: [] },
  ],
}
const snapshot = {
  title: 'Shared preview',
  created_at: metadata.created_at,
  expires_at: metadata.expires_at,
  episodes: [
    {
      index: 1,
      title: 'One',
      segments: [{ speaker: 'Host', text: 'Recorded preview' }],
      audio: null,
    },
  ],
}
let wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => {
  vi.mocked(sharingApi.options).mockResolvedValue(options)
  vi.mocked(sharingApi.links).mockResolvedValue({ configured: true, links: [] })
  vi.mocked(sharingApi.preview).mockResolvedValue({
    snapshot,
    key: 'preview-key',
  })
  vi.mocked(sharingApi.create).mockResolvedValue({
    link: metadata,
    url: 'https://review.example.test/r/token',
  })
  vi.mocked(sharingApi.revoke).mockResolvedValue(undefined)
})
afterEach(() => {
  wrappers.forEach((w) => w.unmount())
  wrappers = []
  vi.clearAllMocks()
})
async function show() {
  const wrapper = mount(ReviewLinkButton, {
    props: { kind: 'series', targetId: 'series-one', ready: true },
    global: { stubs: { Teleport: true } },
  })
  wrappers.push(wrapper)
  await wrapper.find('button').trigger('click')
  await flushPromises()
  return wrapper
}
async function preview(wrapper: ReturnType<typeof mount>) {
  await wrapper.find('.ack input').setValue(true)
  await wrapper.find('form').trigger('submit')
  await flushPromises()
}
describe('owner review links', () => {
  it('requires missing-audio acknowledgement and a preview before creating', async () => {
    const w = await show()
    expect(w.text()).toContain('älteren Skriptstand')
    expect(w.find('form button').attributes('disabled')).toBeDefined()
    await preview(w)
    expect(sharingApi.preview).toHaveBeenCalledWith('series', 'series-one', {
      title: 'Series',
      episodes: [
        { index: 1, title: 'One', take_id: 'take-one' },
        { index: 2, title: 'Two', take_id: null },
      ],
      acknowledge_missing_audio: true,
    })
    expect(w.text()).toContain('Recorded preview')
    const create = w
      .findAll('button')
      .find((b) => b.text() === 'Link erstellen')!
    await create.trigger('click')
    await flushPromises()
    expect(sharingApi.create).toHaveBeenCalledWith(
      'series',
      'series-one',
      expect.any(Object),
      'preview-key',
      null,
    )
    expect(w.find('input[readonly]').element).toHaveProperty(
      'value',
      'https://review.example.test/r/token',
    )
    expect(w.text()).toContain('Der vollständige Link wird nur jetzt angezeigt')
  })
  it('invalidates preview on edits and requires explicit replacement confirmation', async () => {
    vi.mocked(sharingApi.links).mockResolvedValue({
      configured: true,
      links: [metadata],
    })
    const w = await show()
    await preview(w)
    await w
      .findAll('button')
      .find((b) => b.text() === 'Neuen Link erstellen')!
      .trigger('click')
    expect(sharingApi.create).not.toHaveBeenCalled()
    await w
      .findAll('button')
      .find((b) => b.text() === 'Erstellen und ersetzen')!
      .trigger('click')
    await flushPromises()
    expect(sharingApi.create).toHaveBeenCalledWith(
      'series',
      'series-one',
      expect.any(Object),
      'preview-key',
      'link-one',
    )
    await w.find('#share-title').setValue('Changed')
    expect(w.find('.preview').exists()).toBe(false)
  })
  it('does not report revoke success on failure and allows a retry', async () => {
    vi.mocked(sharingApi.links).mockResolvedValue({
      configured: true,
      links: [metadata],
    })
    vi.mocked(sharingApi.revoke).mockRejectedValueOnce(new Error('network'))
    const w = await show()
    await w
      .findAll('button')
      .find((b) => b.text() === 'Widerrufen')!
      .trigger('click')
    await w.find('[aria-label="Link widerrufen"] .btn--danger').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Aktiv')
    expect(w.find('[role="alert"]').exists()).toBe(true)
    await w.find('[aria-label="Link widerrufen"] .btn--danger').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Widerrufen')
    expect(w.text()).not.toContain('Aktiv')
  })
  it('keeps revocation available when a changed target cannot be shared again', async () => {
    vi.mocked(sharingApi.links).mockResolvedValue({
      configured: true,
      links: [metadata],
    })
    vi.mocked(sharingApi.options).mockRejectedValueOnce(
      new ApiError(409, '', 'Serie unvollständig'),
    )
    const w = await show()
    expect(w.text()).toContain('Serie unvollständig')
    expect(w.find('form').exists()).toBe(false)
    await w
      .findAll('button')
      .find((b) => b.text() === 'Widerrufen')!
      .trigger('click')
    await w.find('[aria-label="Link widerrufen"] .btn--danger').trigger('click')
    await flushPromises()
    expect(sharingApi.revoke).toHaveBeenCalledWith(metadata.id)
  })
})
