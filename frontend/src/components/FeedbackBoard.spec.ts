import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { sharingApi, type ReviewLinkRow } from '@/api/sharing'
import FeedbackBoard from './FeedbackBoard.vue'
import ReviewLinkButton from './ReviewLinkButton.vue'

vi.mock('@/api/sharing', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sharing')>()),
  sharingApi: {
    all: vi.fn(),
    options: vi.fn(),
    links: vi.fn(),
    preview: vi.fn(),
    create: vi.fn(),
    revoke: vi.fn(),
  },
}))

const store = {
  items: [] as { id: string; status: string; name: string | null; document_title: string; created_at: string }[],
  load: vi.fn(async () => undefined),
}
vi.mock('@/stores/runs', () => ({ useRunsStore: () => store }))

const runRow: ReviewLinkRow = {
  id: 'link-run',
  target_kind: 'runs',
  target_id: 'run-one',
  target_title: 'Physiologie',
  title: 'Herz und Kreislauf',
  episodes: ['Herz und Kreislauf'],
  created_at: '2026-10-20T10:00:00Z',
  expires_at: '2026-11-19T10:00:00Z',
  status: 'active',
  responses: 3,
  last_activity: '2026-10-21T08:30:00Z',
}
const seriesRow: ReviewLinkRow = {
  ...runRow,
  id: 'link-series',
  target_kind: 'series',
  target_id: 'series-one',
  target_title: null,
  title: 'Die Niere',
  episodes: ['Filter', 'Hormone'],
  status: 'revoked',
  responses: 0,
  last_activity: null,
}

let wrappers: ReturnType<typeof mount>[] = []
function board(props: Record<string, unknown> = {}) {
  const wrapper = mount(FeedbackBoard, {
    props,
    attachTo: document.body,
    global: { stubs: { RouterLink: RouterLinkStub } },
  })
  wrappers.push(wrapper)
  return wrapper
}

beforeEach(() => {
  vi.mocked(sharingApi.all).mockResolvedValue({ configured: true, links: [runRow, seriesRow] })
  vi.mocked(sharingApi.links).mockResolvedValue({ configured: true, links: [] })
  vi.mocked(sharingApi.options).mockResolvedValue({ title: 'Review', episodes: [] })
  store.items = [
    { id: 'run-one', status: 'completed', name: null, document_title: 'Physiologie', created_at: runRow.created_at },
    { id: 'run-busy', status: 'running', name: null, document_title: 'Anatomie', created_at: runRow.created_at },
  ]
})
afterEach(() => {
  wrappers.forEach((w) => w.unmount())
  wrappers = []
  vi.clearAllMocks()
})

describe('FeedbackBoard', () => {
  it('lists every shared stand with its source, state, responses and last activity', async () => {
    const w = board()
    await flushPromises()
    expect(sharingApi.all).toHaveBeenCalledWith(undefined)
    const [first, second] = w.findAll('tbody tr')
    expect(first.text()).toContain('Herz und Kreislauf')
    expect(first.text()).toContain('Aktiv')
    expect(first.find('.num').text()).toBe('3')
    expect(first.text()).not.toContain('Noch keine')
    const links = first.findAllComponents(RouterLinkStub)
    expect(links[0].props('to')).toEqual({ name: 'shared-feedback', params: { id: 'link-run' } })
    // The source opens the run straight on its own Feedback tab.
    expect(links[1].props('to')).toEqual({
      name: 'run',
      params: { id: 'run-one' },
      query: { tab: 'feedback' },
    })
    expect(links[2].text()).toBe('Im Skript ansehen')
    expect(links[2].props('to')).toEqual({ name: 'shared-feedback', params: { id: 'link-run' } })

    expect(second.text()).toContain('2 Folgen')
    expect(second.text()).toContain('Serie')
    expect(second.text()).toContain('Nicht mehr vorhanden')
    expect(second.text()).toContain('Widerrufen')
    expect(second.text()).toContain('Noch keine')
  })

  it('says so when nothing is shared yet and when the list fails', async () => {
    vi.mocked(sharingApi.all).mockResolvedValueOnce({ configured: true, links: [] })
    const empty = board()
    await flushPromises()
    expect(empty.find('[data-empty]').text()).toContain('Noch kein Feedback')

    vi.mocked(sharingApi.all).mockRejectedValueOnce(new ApiError(500, 'Error', ''))
    const failed = board()
    await flushPromises()
    expect(failed.find('[role="alert"]').text()).toContain('konnte nicht geladen werden')
  })

  it('creates feedback for a chosen finished run with the existing share flow', async () => {
    const w = board()
    await flushPromises()
    await w.find('[data-create]').trigger('click')
    await flushPromises()
    expect(store.load).toHaveBeenCalled()
    const picker = document.body.querySelector<HTMLSelectElement>('#feedback-run')!
    const choices = [...picker.options].filter((o) => o.value).map((o) => o.value)
    expect(choices).toEqual(['run-one'])
    expect(w.findComponent(ReviewLinkButton).exists()).toBe(false)

    picker.value = 'run-one'
    picker.dispatchEvent(new Event('change'))
    await flushPromises()
    const share = w.findComponent(ReviewLinkButton)
    expect(share.props()).toMatchObject({ kind: 'runs', targetId: 'run-one', ready: true, inline: true })
    expect(sharingApi.links).toHaveBeenCalledWith('runs', 'run-one')

    share.vm.$emit('changed')
    await flushPromises()
    expect(sharingApi.all).toHaveBeenCalledTimes(2)
  })

  it('shows one run its own feedback and sharing controls', async () => {
    vi.mocked(sharingApi.all).mockResolvedValue({ configured: true, links: [runRow] })
    const w = board({ run: { id: 'run-one', ready: false } })
    await flushPromises()
    expect(sharingApi.all).toHaveBeenCalledWith({ kind: 'runs', id: 'run-one' })
    expect(w.find('[data-create]').exists()).toBe(false)
    expect(w.findAll('th').map((th) => th.text())).not.toContain('Lauf / Serie')
    expect(w.find('tbody tr').text()).toContain('Herz und Kreislauf')
    const share = w.findComponent(ReviewLinkButton)
    expect(share.props()).toMatchObject({ kind: 'runs', targetId: 'run-one', ready: false })
  })
})
