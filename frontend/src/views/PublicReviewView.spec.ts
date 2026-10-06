import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '@/App.vue'
import { createAppRouter } from '@/router'
import { useAuthStore } from '@/stores/auth'
import type { SharedSnapshot } from '@/api/sharing'

const snapshot: SharedSnapshot = {
  title: 'Public series',
  created_at: '2026-10-04T12:00:00Z',
  expires_at: '2026-11-03T12:00:00Z',
  episodes: [
    {
      index: 1,
      title: 'One',
      segments: [{ speaker: 'Host', text: '<img src=x> Recorded script' }],
      audio: {
        url: '/api/public/review-links/token/episodes/1/audio',
        duration_s: 12,
      },
    },
    {
      index: 2,
      title: 'Two',
      segments: [{ speaker: 'Host', text: 'Second script' }],
      audio: null,
    },
    {
      index: 3,
      title: 'Three',
      segments: [{ speaker: 'Host', text: 'Third script' }],
      audio: {
        url: '/api/public/review-links/token/episodes/3/audio',
        duration_s: 15,
      },
    },
  ],
}
const fetchMock = vi.fn()
let cleanup: (() => void)[] = []
beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset().mockImplementation(
    async () =>
      new Response(JSON.stringify(snapshot), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
  )
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
})
afterEach(() => {
  cleanup.forEach((fn) => fn())
  cleanup = []
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})
async function reader(path = '/r/token', signedIn = false) {
  const pinia = createPinia()
  setActivePinia(pinia)
  if (signedIn)
    useAuthStore().user = {
      id: 'owner',
      name: 'Private owner',
      email: 'private@example.test',
      role: 'admin',
      active: true,
    }
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  await router.isReady()
  const wrapper = mount(App, { global: { plugins: [pinia, router] } })
  cleanup.push(() => wrapper.unmount())
  await flushPromises()
  return { wrapper, router }
}
describe('link-only reader through real app routing', () => {
  it('waits for initial routing before considering private bootstrap', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const history = createMemoryHistory()
    history.replace('/r/token')
    const router = createAppRouter(history)
    let release!: () => void
    const navigationGate = new Promise<void>((resolve) => {
      release = resolve
    })
    router.beforeEach(() => navigationGate)
    const wrapper = mount(App, { global: { plugins: [pinia, router] } })
    cleanup.push(() => wrapper.unmount())
    await flushPromises()
    expect(fetchMock).not.toHaveBeenCalled()
    release()
    await router.isReady()
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/public/review-links/token')
    expect(fetchMock.mock.calls[1][0]).toBe(
      '/api/public/review-links/token/feedback',
    )
    expect(wrapper.find('.rail').exists()).toBe(false)
  })
  it.each([false, true])(
    'skips private bootstrap and chrome (signed in %s)',
    async (signedIn) => {
      const { wrapper } = await reader('/r/token', signedIn)
      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(fetchMock).toHaveBeenCalledWith('/api/public/review-links/token', {
        credentials: 'omit',
        cache: 'no-store',
      })
      expect(wrapper.find('.rail').exists()).toBe(false)
      expect(wrapper.text()).not.toContain('Private owner')
      expect(wrapper.find('.segment img').exists()).toBe(false)
      expect(wrapper.find('.segment .prose').text()).toContain('<img src=x>')
      expect(wrapper.find('audio').attributes('src')).toContain(
        '/api/public/review-links/token/',
      )
    },
  )
  it('restores an episode from its URL, pauses old playback and preserves the bearer route', async () => {
    const { wrapper, router } = await reader('/r/token?episode=1')
    await wrapper.findAll('.episodes button')[1].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/r/token')
    expect(router.currentRoute.value.query.episode).toBe('2')
    expect(wrapper.text()).toContain('Second script')
    expect(wrapper.find('audio').exists()).toBe(false)
    expect(wrapper.text()).toContain(
      'Für diesen geteilten Stand ist kein Audio enthalten',
    )
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled()
    await wrapper.findAll('.reading footer button')[1].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Third script')
    expect(wrapper.find('audio').exists()).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
  it('opens a series deep link and falls back for an invalid ordinal', async () => {
    const { wrapper, router } = await reader('/r/token?episode=2')
    expect(wrapper.text()).toContain('Second script')
    expect(wrapper.find('audio').exists()).toBe(false)
    await router.push('/r/token?episode=999')
    await flushPromises()
    expect(wrapper.text()).toContain('Die erste geteilte Folge wird angezeigt')
    expect(wrapper.text()).toContain('Recorded script')
  })
  it.each([404, 410])(
    'shows generic unavailable state for %s without content',
    async (status) => {
      fetchMock.mockImplementation(async () => new Response('{}', { status }))
      const { wrapper } = await reader()
      expect(wrapper.text()).toContain('Dieser Review-Link ist nicht verfügbar')
      expect(wrapper.find('.shared-content').exists()).toBe(false)
    },
  )
  it('separates a transient error from revoked access and retries', async () => {
    fetchMock.mockImplementationOnce(
      async () => new Response('{}', { status: 503 }),
    )
    const { wrapper } = await reader()
    expect(wrapper.text()).toContain(
      'Der geteilte Stand konnte nicht geladen werden',
    )
    await wrapper.find('.state button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Recorded script')
  })
  it('keeps the script readable after a player error and retries the media', async () => {
    const { wrapper } = await reader()
    await wrapper.find('audio').trigger('error')
    expect(wrapper.text()).toContain('Audio kann gerade nicht geladen werden')
    expect(wrapper.text()).toContain('Recorded script')
    await wrapper.find('.notice--fail button').trigger('click')
    expect(HTMLMediaElement.prototype.load).toHaveBeenCalled()
  })
  it('shows loading and explains an empty shared snapshot', async () => {
    let finish!: (response: Response) => void
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve
        }),
    )
    const { wrapper } = await reader()
    expect(wrapper.text()).toContain('Geteilter Stand wird geladen')
    finish(
      new Response(JSON.stringify({ ...snapshot, episodes: [] }), {
        status: 200,
      }),
    )
    await flushPromises()
    expect(wrapper.text()).toContain('Kein Skript verfügbar')
    expect(wrapper.find('audio').exists()).toBe(false)
  })
  it('toggles a line mark and offers the sheet when every recording ends', async () => {
    vi.useFakeTimers()
    const marks: {
      episode: number
      ordinal: number
      reaction: string | null
      slop: boolean
      comment: string | null
    }[] = []
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      const target = String(url)
      if (target.endsWith('/feedback/marks')) {
        const body = JSON.parse(String(init?.body))
        marks.splice(0, marks.length)
        if (body.reaction || body.comment) marks.push(body)
        return new Response(
          JSON.stringify({
            label: null,
            stars: null,
            worked: null,
            did_not: null,
            marks,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (target.endsWith('/feedback')) {
        return new Response(
          JSON.stringify({
            label: null,
            stars: null,
            worked: null,
            did_not: null,
            marks,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response(JSON.stringify(snapshot), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    const { wrapper } = await reader('/r/token?episode=1')
    await wrapper.find('.comment-toggle').trigger('click')
    await wrapper.find('.segment textarea').setValue('Comment without rating')
    await vi.advanceTimersByTimeAsync(350)
    await flushPromises()
    expect(marks[0]).toMatchObject({ reaction: null, comment: 'Comment without rating' })
    const commentOnlyReturn = await reader('/r/token?episode=1')
    await commentOnlyReturn.wrapper.find('.comment-toggle').trigger('click')
    expect((commentOnlyReturn.wrapper.find('.segment textarea').element as HTMLTextAreaElement).value).toBe('Comment without rating')
    commentOnlyReturn.wrapper.unmount()
    await wrapper.find('.comment-toggle').trigger('click')
    const impressed = wrapper
      .findAll('button')
      .find((button) => button.attributes('aria-label') === 'Beeindruckt')!
    await impressed.trigger('click')
    await vi.advanceTimersByTimeAsync(1100)
    await flushPromises()
    expect(marks[0]?.reaction).toBe('impressed')
    expect(impressed.attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('.segment textarea').exists()).toBe(false)
    await wrapper.find('.comment-toggle').trigger('click')
    await wrapper.find('.segment textarea').setValue('Independent comment')
    await flushPromises()
    expect(marks[0]?.comment).toBe('Comment without rating')
    await vi.advanceTimersByTimeAsync(1100)
    await flushPromises()
    expect(marks[0]?.comment).toBe('Independent comment')
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/feedback/sheet'))).toBe(false)
    const returned = await reader('/r/token?episode=1')
    expect(returned.wrapper.find('[aria-label="Beeindruckt"]').attributes('aria-pressed')).toBe('true')
    await returned.wrapper.find('.comment-toggle').trigger('click')
    expect((returned.wrapper.find('.segment textarea').element as HTMLTextAreaElement).value).toBe('Independent comment')
    returned.wrapper.unmount()
    expect(
      wrapper.findAll('button').some((button) => button.text() === 'Klingt nach KI-Slop'),
    ).toBe(false)
    await impressed.trigger('click')
    await flushPromises()
    expect(impressed.attributes('aria-pressed')).toBe('false')
    expect(marks[0]?.reaction).toBe('impressed')
    await vi.advanceTimersByTimeAsync(1100)
    await flushPromises()
    expect(marks[0]).toMatchObject({ reaction: null, comment: 'Independent comment' })
    const clearedReturn = await reader('/r/token?episode=1')
    expect(clearedReturn.wrapper.find('[aria-label="Beeindruckt"]').attributes('aria-pressed')).toBe('false')
    await clearedReturn.wrapper.find('.comment-toggle').trigger('click')
    expect((clearedReturn.wrapper.find('.segment textarea').element as HTMLTextAreaElement).value).toBe('Independent comment')
    clearedReturn.wrapper.unmount()
    const questionnaireTriggers = wrapper
      .findAll('button')
      .filter((button) => button.text() === 'Feedback')
    expect(questionnaireTriggers).toHaveLength(1)
    const headerTrigger = wrapper.find('.shared-content > .head button')
    expect(questionnaireTriggers[0].element).toBe(headerTrigger.element)
    expect(headerTrigger.attributes('type')).toBe('button')
    expect(wrapper.find('.playback-bar button').exists()).toBe(false)
    await headerTrigger.trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('Was hat funktioniert?')
    document.querySelector<HTMLButtonElement>('.panel__head button')!.click()
    await flushPromises()
    await wrapper.find('audio').trigger('ended')
    await flushPromises()
    expect(document.body.textContent).toContain(
      'Das zeigt nicht, dass jemand zugehört hat',
    )
    document.querySelector<HTMLButtonElement>('.panel__head button')!.click()
    await flushPromises()
    await wrapper.findAll('.reading footer button')[1].trigger('click')
    await flushPromises()
    await wrapper.findAll('.reading footer button')[1].trigger('click')
    await flushPromises()
    await wrapper.find('audio').trigger('ended')
    await flushPromises()
    expect(document.body.textContent).toContain(
      'Das zeigt nicht, dass jemand zugehört hat',
    )
    expect(wrapper.find('.segment textarea').exists()).toBe(false)
  })
})
