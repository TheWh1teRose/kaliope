import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { sharingApi, type FeedbackScript } from '@/api/sharing'
import { ApiError } from '@/api/client'
import SharedFeedbackView from './SharedFeedbackView.vue'

vi.mock('@/api/sharing', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/sharing')>()),
  sharingApi: { feedbackScript: vi.fn() },
}))

const response = (index: number, label: string | null, stars: number | null) => ({
  index,
  label,
  stars,
  worked: index === 1 ? 'Tempo' : null,
  did_not: null,
  impressed: 0,
  dislike: 0,
  horrible: 0,
  slop: 0,
})
const view: FeedbackScript = {
  link: {
    id: 'link-one',
    created_at: '2026-10-04T12:00:00Z',
    expires_at: '2026-11-03T12:00:00Z',
    status: 'revoked',
    feedback: {
      responses: [response(1, 'Lena', 4), response(2, null, 2.5), response(3, null, null)],
      impressed: 1,
      dislike: 1,
      horrible: 0,
      slop: 1,
      lines: [],
    },
  },
  target_kind: 'series',
  target_id: 'series-one',
  title: 'Mond',
  episodes: [
    {
      index: 2,
      title: 'Bahn',
      audio: { url: '/api/review-links/link-one/episodes/2/audio', duration_s: 20 },
      lines: [
        {
          ordinal: 0,
          start_s: 0,
          end_s: 15,
          speaker: 'Mara',
          text: 'Warum fällt der Mond nicht?',
          voices: [
            { response: 1, reaction: 'dislike', slop: true, comment: 'Klingt glatt' },
            { response: 2, reaction: 'impressed', slop: false, comment: null },
            { response: 3, reaction: null, slop: false, comment: 'Quelle?' },
          ],
        },
        { ordinal: 1, start_s: 15, end_s: 20, speaker: 'Jonas', text: 'Er fällt.', voices: [] },
      ],
    },
  ],
}

let wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => {
  vi.mocked(sharingApi.feedbackScript).mockReset().mockResolvedValue(structuredClone(view))
})
afterEach(() => {
  for (const wrapper of wrappers) wrapper.unmount()
  wrappers = []
})

async function show() {
  const wrapper = mount(SharedFeedbackView, {
    props: { id: 'link-one' },
    global: { stubs: { RouterLink: RouterLinkStub } },
    attachTo: document.body,
  })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

describe('SharedFeedbackView', () => {
  it('puts every voice beside the frozen line it marks', async () => {
    const w = await show()
    expect(sharingApi.feedbackScript).toHaveBeenCalledWith('link-one')
    const [marked, quiet] = w.findAll('article.line')
    expect(marked.find('.key').text()).toBe('e2-s00')
    expect(marked.find('.head').text()).toContain('👍 1')
    expect(marked.find('.head').text()).toContain('🤢 1')
    expect(marked.find('.head').text()).toContain('Klingt nach KI-Slop 1')
    expect(marked.find('.head').text()).toContain('💬 2')
    const notes = marked.findAll('.note')
    expect(notes.map((n) => n.find('b').text())).toEqual(['Lena', 'Rückmeldung 3'])
    expect(notes[0].text()).toContain('Klingt glatt')
    expect(notes[0].find('.slop').exists()).toBe(true)
    expect(notes[1].text()).toContain('💬')
    expect(notes[1].text()).not.toMatch(/👍|🤢|🤮/)
    expect(marked.find('.quick').text()).toContain('👍 Rückmeldung 2')
    expect(quiet.classes()).toContain('quiet')
    expect(quiet.find('.margin').text()).toBe('')
    expect(w.find('.back').exists()).toBe(true)
    expect(w.findComponent(RouterLinkStub).props('to')).toEqual({
      name: 'series',
      params: { id: 'series-one' },
    })
    expect(w.text()).toContain('Widerrufen')
  })

  it('summarises the questionnaire and filters people everywhere', async () => {
    const w = await show()
    expect(w.find('.average').text()).toBe('3,3')
    expect(w.find('.score').text()).toContain('2 von 3 mit Sternen')
    expect(w.find('.answers').text()).toContain('Tempo')
    expect(w.find('.answers').text()).toContain('Kein Fragebogen')
    await w.findAll('.person')[0].trigger('click')
    expect(w.findAll('.person')[0].attributes('aria-pressed')).toBe('false')
    expect(w.find('.average').text()).toBe('2,5')
    const marked = w.find('article.line')
    expect(marked.findAll('.note').map((n) => n.find('b').text())).toEqual(['Rückmeldung 3'])
    expect(marked.find('.head').text()).not.toContain('🤢')
    expect(w.find('.strip .segment').attributes('aria-label')).toBe('e2-s00: 2 Stimmen')
  })

  it('sizes the strip by speaking time and plays from a line', async () => {
    const w = await show()
    const segments = w.findAll('.strip .segment')
    expect(segments[0].attributes('style')).toContain('flex-grow: 15')
    expect(segments[1].attributes('style')).toContain('flex-grow: 5')
    expect(w.find('.overview').text()).toContain('Breite = Sprechdauer')
    const audio = w.find('audio').element as HTMLAudioElement
    expect(audio.getAttribute('src')).toBe('/api/review-links/link-one/episodes/2/audio')
    const play = vi.spyOn(audio, 'play').mockResolvedValue()
    await w.findAll('.play')[1].trigger('click')
    expect(audio.currentTime).toBe(15)
    expect(play).toHaveBeenCalled()
    await w.find('audio').trigger('timeupdate')
    expect(w.findAll('article.line')[1].attributes('aria-current')).toBe('true')
  })

  it('falls back to equal widths without a recording', async () => {
    const plain = structuredClone(view)
    plain.episodes[0].audio = null
    plain.episodes[0].lines = plain.episodes[0].lines.map((line) => ({ ...line, start_s: null, end_s: null }))
    vi.mocked(sharingApi.feedbackScript).mockResolvedValue(plain)
    const w = await show()
    expect(w.find('audio').exists()).toBe(false)
    expect(w.find('.play').exists()).toBe(false)
    expect(w.findAll('.strip .segment')[0].attributes('style')).toContain('flex-grow: 1')
    expect(w.find('.overview').text()).toContain('gleich breit')
  })

  it('jumps from the strip to the line', async () => {
    const w = await show()
    const scroll = vi.fn()
    ;(document.getElementById('line-1') as HTMLElement).scrollIntoView = scroll
    await w.findAll('.strip .segment')[1].trigger('click')
    expect(scroll).toHaveBeenCalled()
    expect(w.findAll('article.line')[1].classes()).toContain('focused')
  })

  it('reports a stand that cannot be loaded', async () => {
    vi.mocked(sharingApi.feedbackScript).mockRejectedValue(new ApiError(404, '', ''))
    const w = await show()
    expect(w.find('[role="alert"]').text()).toBe('Die Rückmeldungen konnten nicht geladen werden.')
  })
})
