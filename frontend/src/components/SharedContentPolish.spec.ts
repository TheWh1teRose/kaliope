import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SharedContent from './SharedContent.vue'
import { sharingApi, type FeedbackState, type SharedSnapshot } from '@/api/sharing'

const empty: FeedbackState = { label: null, stars: null, worked: null, did_not: null, marks: [] }
const snapshot: SharedSnapshot = {
  title: 'Review', created_at: '', expires_at: '', episodes: [1, 2].map(index => ({
    index, title: `Episode ${index}`, audio: { url: `/audio/${index}`, duration_s: 20 },
    segments: [
      { ordinal: 0, speaker: 'Host', text: 'First', start_s: 1, end_s: 4 },
      { ordinal: 1, speaker: 'Guest', text: 'Second', start_s: 6, end_s: 12 },
      { ordinal: 2, speaker: 'Host', text: 'No alignment' },
    ],
  })),
}
function deferred<T>() {
  let resolve!: (data: T) => void
  const promise = new Promise<T>(yes => { resolve = yes })
  return { promise, resolve }
}
let wrappers: ReturnType<typeof mount>[] = []
function reader() {
  const wrapper = mount(SharedContent, { props: { snapshot, selected: 1, token: 'token' },
    global: { stubs: { teleport: true } } })
  wrappers.push(wrapper)
  return wrapper
}
afterEach(() => { wrappers.forEach(w => w.unmount()); wrappers = []; vi.useRealTimers(); vi.restoreAllMocks() })

describe('public feedback polish', () => {
  it('highlights actual aligned intervals on play, seek and pause; resets for episode changes', async () => {
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(empty)
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
    const w = reader()
    await flushPromises()
    const audio = w.find('audio')
    async function clock(time: number, event: string) {
      ;(audio.element as HTMLAudioElement).currentTime = time
      await audio.trigger(event)
    }
    expect(w.find('[aria-current="true"].segment').exists()).toBe(false)
    await clock(2, 'play')
    expect(w.find('.segment--playing').text()).toContain('First')
    await clock(7, 'seeking')
    expect(w.find('.segment--playing').text()).toContain('Second')
    await clock(8, 'pause')
    expect(w.find('.segment--playing').text()).toContain('Second')
    const script = w.find('.script').element as HTMLElement
    script.scrollTop = 80
    await clock(9, 'timeupdate')
    expect(script.scrollTop).toBe(80) // Playback never traps manual scrolling.
    expect(w.findAll('button').some(b => b.text() === 'Aktuelle Passage finden')).toBe(true)
    await clock(4, 'seeked')
    expect(w.find('.segment--playing').exists()).toBe(false) // Real silence, not uniform timing.
    await clock(11, 'timeupdate')
    await w.setProps({ selected: 2 })
    expect(w.find('.segment--playing').exists()).toBe(false)
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled()
  })

  it('blocks sheet submission until hydration, preserving intentional questionnaire edits', async () => {
    const load = deferred<FeedbackState>()
    vi.spyOn(sharingApi, 'feedback').mockReturnValue(load.promise)
    const save = vi.spyOn(sharingApi, 'saveSheet').mockImplementation(async (_, body) => ({ ...empty, ...body }))
    const w = reader()
    await w.find('.head button').trigger('click')
    await w.find('#share-worked').setValue('New draft')
    expect(w.find('.panel__foot .btn--primary').attributes('disabled')).toBeDefined()
    await w.find('.panel__foot .btn--primary').trigger('click')
    expect(save).not.toHaveBeenCalled()
    load.resolve({ ...empty, stars: 4, worked: 'Stored', did_not: 'Stored negative' })
    await flushPromises()
    await w.find('.panel__foot .btn--primary').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith('token', { label: null, stars: 4, worked: 'New draft', did_not: 'Stored negative' })
  })

  it('saves input-only optional negative comments independently, and restores them on return', async () => {
    vi.useFakeTimers()
    let state = { ...empty }
    vi.spyOn(sharingApi, 'feedback').mockImplementation(async () => state)
    const save = vi.spyOn(sharingApi, 'saveMark').mockImplementation(async (_, body) => {
      state = { ...state, marks: body.reaction ? [{ ...body, reaction: body.reaction }] : [] }
      return state
    })
    const sheet = vi.spyOn(sharingApi, 'saveSheet')
    const w = reader()
    await flushPromises()
    await w.find('[aria-label="Furchtbar"]').trigger('click')
    await flushPromises()
    expect(w.find('.segment textarea').exists()).toBe(false)
    expect(w.text()).not.toContain('KI-Slop')
    await w.find('.comment-toggle').trigger('click')
    const input = w.find('.segment textarea')
    ;(input.element as HTMLTextAreaElement).value = 'This was confusing'
    await input.trigger('input') // No change, blur or questionnaire submit.
    await vi.advanceTimersByTimeAsync(350)
    await flushPromises()
    expect(save.mock.calls.at(-1)![1].comment).toBe('This was confusing')
    expect(sheet).not.toHaveBeenCalled()
    w.unmount(); wrappers = []
    const returned = reader()
    await flushPromises()
    expect(returned.find('[aria-label="Furchtbar"]').attributes('aria-pressed')).toBe('true')
    await returned.find('.comment-toggle').trigger('click')
    expect((returned.find('textarea').element as HTMLTextAreaElement).value).toBe('This was confusing')
    await returned.find('[aria-label="Furchtbar"]').trigger('click')
    await flushPromises()
    expect(state.marks).toEqual([])
  })

  it('orders a comment queued behind a slow reaction, protects newer input and warns before leaving', async () => {
    vi.useFakeTimers()
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(empty)
    const first = deferred<FeedbackState>()
    const save = vi.spyOn(sharingApi, 'saveMark').mockReturnValueOnce(first.promise)
      .mockImplementation(async (_, body) => ({ ...empty, marks: body.reaction ? [{ ...body, reaction: body.reaction }] : [] }))
    const w = reader()
    await flushPromises()
    await w.find('[aria-label="Mag ich nicht"]').trigger('click')
    await w.find('.comment-toggle').trigger('click')
    const input = w.find('.segment textarea')
    ;(input.element as HTMLTextAreaElement).value = 'Draft while reaction saves'
    await input.trigger('input')
    const leave = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(leave)
    expect(leave.defaultPrevented).toBe(true)
    await flushPromises()
    expect(save).toHaveBeenCalledTimes(1)
    first.resolve({ ...empty, marks: [{ episode: 1, ordinal: 0, reaction: 'dislike', slop: false, comment: null }] })
    await flushPromises()
    expect(save.mock.calls[1][1].comment).toBe('Draft while reaction saves')
    expect((input.element as HTMLTextAreaElement).value).toBe('Draft while reaction saves')
    const safeLeave = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(safeLeave)
    expect(safeLeave.defaultPrevented).toBe(false)
  })
})
