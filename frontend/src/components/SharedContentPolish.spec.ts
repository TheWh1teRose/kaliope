import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SharedContent from './SharedContent.vue'
import { ApiError } from '@/api/client'
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
  it.each([false, true])('scopes questionnaire rate-limit retries and errors to the sheet (retry fails: %s)', async fails => {
    vi.useFakeTimers()
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(empty)
    const mark = vi.spyOn(sharingApi, 'saveMark')
    const sheet = vi.spyOn(sharingApi, 'saveSheet')
      .mockRejectedValueOnce(new ApiError(429, '', 'limited'))
    if (fails) sheet.mockRejectedValueOnce(new ApiError(429, '', 'limited'))
    sheet.mockImplementation(async (_, body) => ({ ...empty, ...body }))
    const w = reader()
    await flushPromises()
    await w.find('.head button').trigger('click')
    await w.find('#share-worked').setValue('Questionnaire draft')
    await w.find('.panel__foot .btn--primary').trigger('click')
    await flushPromises()
    expect(sheet).toHaveBeenCalledTimes(1)
    expect(w.find('.panel__foot .btn--primary').attributes('disabled')).toBeDefined()
    expect(w.find('[role="alert"]').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(59_999)
    expect(sheet).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(sheet).toHaveBeenCalledTimes(2)
    expect(w.find('.reading [role="alert"]').exists()).toBe(false)
    expect(w.find('.panel__foot .btn--primary').attributes('disabled')).toBeUndefined()
    if (fails) {
      expect(w.find('.panel [role="alert"]').text()).toBe('Die Rückmeldung konnte nicht gesendet werden.')
      expect(w.text()).not.toContain('Rückmeldung gesendet.')
      expect((w.find('#share-worked').element as HTMLTextAreaElement).value).toBe('Questionnaire draft')
      await w.find('.panel__foot .btn--primary').trigger('click')
      await flushPromises()
      expect(w.find('.panel [role="alert"]').exists()).toBe(false)
      await vi.advanceTimersByTimeAsync(60_000)
      expect(sheet).toHaveBeenCalledTimes(3)
    }
    expect(w.text()).toContain('Rückmeldung gesendet.')
    expect(w.find('[role="alert"]').exists()).toBe(false)
    expect(sheet).toHaveBeenLastCalledWith('token', { label: null, stars: null, worked: 'Questionnaire draft', did_not: null })
    expect(mark).not.toHaveBeenCalled()
  })

  it('preserves a failed dirty mark when a questionnaire retry succeeds', async () => {
    vi.useFakeTimers()
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(empty)
    const mark = vi.spyOn(sharingApi, 'saveMark').mockRejectedValue(new Error('offline'))
    const sheet = vi.spyOn(sharingApi, 'saveSheet')
      .mockRejectedValueOnce(new ApiError(429, '', 'limited'))
      .mockResolvedValue(empty)
    const w = reader()
    await flushPromises()
    await w.find('[aria-label="Mag ich nicht"]').trigger('click')
    await flushPromises()
    expect(w.find('.reading [role="alert"]').text()).toContain('Die Markierung konnte nicht gespeichert werden.')
    await w.find('.head button').trigger('click')
    await w.find('.panel__foot .btn--primary').trigger('click')
    await vi.advanceTimersByTimeAsync(1100)
    expect(sheet).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(sheet).toHaveBeenCalledTimes(2)
    expect(w.text()).toContain('Rückmeldung gesendet.')
    expect(w.find('.panel [role="alert"]').exists()).toBe(false)
    expect(w.find('.reading [role="alert"]').text()).toContain('Die Markierung konnte nicht gespeichert werden.')
    expect(w.find('[aria-label="Mag ich nicht"]').attributes('aria-pressed')).toBe('true')
    expect(mark).toHaveBeenCalledTimes(1)
    const leave = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(leave)
    expect(leave.defaultPrevented).toBe(true)
  })

  it('bounds continuous typing writes and persists the latest unfinished draft', async () => {
    vi.useFakeTimers()
    let state = { ...empty }
    vi.spyOn(sharingApi, 'feedback').mockImplementation(async () => state)
    const times: number[] = []
    const save = vi.spyOn(sharingApi, 'saveMark').mockImplementation(async (_, body) => {
      times.push(Date.now())
      state = { ...empty, marks: body.reaction ? [{ ...body, reaction: body.reaction }] : [] }
      return state
    })
    const w = reader()
    await flushPromises()
    await w.find('[aria-label="Mag ich nicht"]').trigger('click')
    await w.find('.comment-toggle').trigger('click')
    const input = w.find('.segment textarea')
    for (let n = 1; n <= 130; n++) {
      ;(input.element as HTMLTextAreaElement).value = 'x'.repeat(n)
      await input.trigger('input')
      await vi.advanceTimersByTimeAsync(500)
    }
    await vi.advanceTimersByTimeAsync(1100)
    expect(state.marks[0].comment).toBe('x'.repeat(130))
    expect(save.mock.calls.length).toBeLessThan(65)
    expect(times.slice(1).every((time, index) => time - times[index] >= 1100)).toBe(true)
    w.unmount(); wrappers = []
    const returned = reader()
    await flushPromises()
    await returned.find('.comment-toggle').trigger('click')
    expect((returned.find('textarea').element as HTMLTextAreaElement).value).toBe('x'.repeat(130))
  })

  it('retains 429 drafts, retries the latest clear after cooldown, and paces questionnaire writes', async () => {
    vi.useFakeTimers()
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(empty)
    const save = vi.spyOn(sharingApi, 'saveMark').mockRejectedValueOnce(new ApiError(429, '', 'limited'))
      .mockImplementation(async (_, body) => ({ ...empty, marks: body.reaction ? [{ ...body, reaction: body.reaction }] : [] }))
    const sheet = vi.spyOn(sharingApi, 'saveSheet').mockResolvedValue(empty)
    const w = reader()
    await flushPromises()
    const reaction = w.find('[aria-label="Mag ich nicht"]')
    await reaction.trigger('click')
    await flushPromises()
    expect(reaction.attributes('aria-pressed')).toBe('true')
    expect(w.find('[role="alert"]').exists()).toBe(true)
    await w.find('.comment-toggle').trigger('click')
    await w.find('.segment textarea').setValue('Retained draft')
    await reaction.trigger('click')
    await w.find('.head button').trigger('click')
    await w.find('#share-worked').setValue('Questionnaire')
    await w.find('.panel__foot .btn--primary').trigger('click')
    await vi.advanceTimersByTimeAsync(59_999)
    expect(save).toHaveBeenCalledTimes(1)
    expect(sheet).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(save.mock.calls[1][1]).toMatchObject({ reaction: null, comment: null })
    await vi.advanceTimersByTimeAsync(1100)
    expect(sheet).toHaveBeenCalledTimes(1)
    expect(w.find('[aria-label="Mag ich nicht"]').attributes('aria-pressed')).toBe('false')
  })

  it('stops automatic retries after repeated 429 and retains comments for explicit retry', async () => {
    vi.useFakeTimers()
    const marked: FeedbackState = { ...empty, marks: [{ episode: 1, ordinal: 0, reaction: 'dislike', slop: false, comment: null }] }
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(marked)
    const save = vi.spyOn(sharingApi, 'saveMark')
      .mockRejectedValueOnce(new ApiError(429, '', 'limited'))
      .mockRejectedValueOnce(new ApiError(429, '', 'limited'))
      .mockImplementation(async (_, body) => ({ ...empty, marks: [{ ...body, reaction: 'dislike' }] }))
    const w = reader()
    await flushPromises()
    await w.find('.comment-toggle').trigger('click')
    await w.find('.segment textarea').setValue('Keep this')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(save).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(120_000)
    expect(save).toHaveBeenCalledTimes(2)
    expect((w.find('textarea').element as HTMLTextAreaElement).value).toBe('Keep this')
    const leave = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(leave)
    expect(leave.defaultPrevented).toBe(true)
    await w.find('[role="alert"] button').trigger('click')
    await flushPromises()
    expect(save.mock.calls[2][1].comment).toBe('Keep this')
    expect(w.find('[role="alert"]').exists()).toBe(false)
  })
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
    await vi.advanceTimersByTimeAsync(1100)
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
    await vi.advanceTimersByTimeAsync(1100)
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
    await vi.advanceTimersByTimeAsync(1100)
    expect(save.mock.calls[1][1].comment).toBe('Draft while reaction saves')
    expect((input.element as HTMLTextAreaElement).value).toBe('Draft while reaction saves')
    const safeLeave = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(safeLeave)
    expect(safeLeave.defaultPrevented).toBe(false)
  })
})
