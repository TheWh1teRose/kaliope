import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import SharedContent from './SharedContent.vue'
import { rememberLabel, sharingApi, type FeedbackState, type SharedSnapshot } from '@/api/sharing'

const empty: FeedbackState = { label: null, stars: null, worked: null, did_not: null, marks: [] }
const snapshot: SharedSnapshot = {
  title: 'Review', created_at: '', expires_at: '',
  episodes: [{ index: 1, title: 'Episode', audio: null,
    pages: [0, 1].map(page => ({ page, url: `/page/${page}`, width: 100, height: 100 })),
    segments: [{ ordinal: 0, speaker: 'Host', text: 'Line', citations: [{ rects: [
      { page: 0, bbox: [10, 10, 20, 20] }, { page: 1, bbox: [30, 30, 40, 40] },
    ] }] }],
  }],
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
let cleanup: (() => void)[] = []
beforeEach(() => vi.useFakeTimers())
afterEach(() => { cleanup.forEach(fn => fn()); cleanup = []; vi.useRealTimers(); vi.restoreAllMocks(); rememberLabel('') })
function reader(sharedSnapshot: SharedSnapshot = snapshot) {
  const wrapper = mount(SharedContent, {
    props: { snapshot: sharedSnapshot, selected: 1, token: 'token' },
    global: { stubs: { teleport: true } },
  })
  cleanup.push(() => wrapper.unmount())
  return wrapper
}
describe('feedback persistence', () => {
  it('loads all saved lines before permitting mutations', async () => {
    const load = deferred<FeedbackState>()
    vi.spyOn(sharingApi, 'feedback').mockReturnValue(load.promise)
    const saved: FeedbackState = { ...empty, marks: [{ episode: 1, ordinal: 1,
      reaction: 'dislike', slop: true, comment: 'Saved comment' }] }
    const save = vi.spyOn(sharingApi, 'saveMark').mockResolvedValue(saved)
    const wrapper = reader({ ...snapshot, episodes: [{ ...snapshot.episodes[0], segments: [
      snapshot.episodes[0].segments[0], { ordinal: 1, speaker: 'Host', text: 'Line B' },
    ] }] })
    const first = wrapper.findAll('[aria-label="Beeindruckt"]')[0]
    expect(first.attributes('disabled')).toBeDefined()
    await first.trigger('click')
    await flushPromises()
    expect(save).not.toHaveBeenCalled()
    load.resolve(saved)
    await flushPromises()
    expect(first.attributes('disabled')).toBeUndefined()
    expect(wrapper.findAll('[aria-label="Mag ich nicht"]')[1].attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('.segment textarea').exists()).toBe(false)
    await wrapper.findAll('.comment-toggle')[1].trigger('click')
    expect((wrapper.find('.segment textarea').element as HTMLTextAreaElement).value).toBe('Saved comment')
    await first.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[aria-label="Mag ich nicht"]')[1].attributes('aria-pressed')).toBe('true')
    await wrapper.findAll('[aria-label="Furchtbar"]')[1].trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1100)
    expect(save.mock.calls[1][1]).toMatchObject({ ordinal: 1, reaction: 'horrible', slop: true, comment: 'Saved comment' })
  })
  it('keeps line writes disabled after feedback loading fails but leaves the questionnaire available', async () => {
    vi.spyOn(sharingApi, 'feedback').mockRejectedValue(new Error('failed'))
    const save = vi.spyOn(sharingApi, 'saveMark').mockResolvedValue(empty)
    const wrapper = reader()
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toContain('nicht geladen')
    expect(wrapper.find('[aria-label="Beeindruckt"]').attributes('disabled')).toBeDefined()
    await wrapper.find('[aria-label="Beeindruckt"]').trigger('click')
    expect(save).not.toHaveBeenCalled()
    await wrapper.find('.head button').trigger('click')
    expect(wrapper.find('#share-worked').attributes('disabled')).toBeUndefined()
  })
  it('hides reviewer names and keeps legacy labels without rewriting them', async () => {
    rememberLabel('Lena')
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue({ ...empty, label: 'Lena' })
    const save = vi.spyOn(sharingApi, 'saveSheet').mockResolvedValue({ ...empty, label: 'Lena' })
    const wrapper = reader()
    await flushPromises()
    await wrapper.find('.head button').trigger('click')
    expect(wrapper.find('#share-label').exists()).toBe(false)
    await wrapper.find('.panel__foot .btn--primary').trigger('click')
    await flushPromises()
    expect(wrapper.find('#share-label').exists()).toBe(false)
    await wrapper.find('.panel__foot .btn--primary').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1100)
    expect(save.mock.calls.map(call => call[1].label)).toEqual(['Lena', 'Lena'])
  })
  it.each(['reaction', 'comment', 'rollback'])('preserves input-only line drafts across a pending %s save', async action => {
    const marked: FeedbackState = { ...empty, marks: [{ episode: 1, ordinal: 0,
      reaction: 'dislike', slop: false, comment: 'Saved' }] }
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(action === 'reaction' ? empty : marked)
    const pending = deferred<FeedbackState>()
    const save = vi.spyOn(sharingApi, 'saveMark').mockReturnValueOnce(pending.promise).mockResolvedValue(marked)
    const wrapper = reader()
    await flushPromises()
    if (action === 'reaction') await wrapper.find('[aria-label="Mag ich nicht"]').trigger('click')
    await wrapper.find('.comment-toggle').trigger('click')
    if (action !== 'reaction') await wrapper.find('.segment textarea').setValue('Submitted')
    const textarea = wrapper.find('.segment textarea')
    ;(textarea.element as HTMLTextAreaElement).value = 'Unsent draft'
    await textarea.trigger('input')
    if (action === 'rollback') pending.reject(new Error('failed'))
    else pending.resolve(marked)
    await flushPromises()
    expect((wrapper.find('.segment textarea').element as HTMLTextAreaElement).value).toBe('Unsent draft')
    expect(save).toHaveBeenCalledTimes(1)
    await wrapper.find('.segment textarea').trigger('change')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1100)
    expect(save.mock.calls[1][1].comment).toBe('Unsent draft')
  })
  it('preserves the line comment draft when a reaction is explicitly cleared', async () => {
    const marked: FeedbackState = { ...empty, marks: [{ episode: 1, ordinal: 0,
      reaction: 'impressed', slop: false, comment: 'Saved' }] }
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(marked)
    const save = vi.spyOn(sharingApi, 'saveMark').mockResolvedValueOnce(empty).mockResolvedValue(marked)
    const wrapper = reader()
    await flushPromises()
    await wrapper.find('.comment-toggle').trigger('click')
    const textarea = wrapper.find('.segment textarea')
    ;(textarea.element as HTMLTextAreaElement).value = 'Old draft'
    await textarea.trigger('input')
    await wrapper.find('[aria-label="Beeindruckt"]').trigger('click')
    await wrapper.find('[aria-label="Beeindruckt"]').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1100)
    expect(save.mock.calls.at(-1)![1].comment).toBe('Old draft')
  })
  it('retains a multi-page citation while navigating forward and back', async () => {
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(empty)
    const wrapper = reader()
    await flushPromises()
    await wrapper.find('.cites button').trigger('click')
    expect(wrapper.find('rect.mark').attributes('x')).toBe('8.5')
    await wrapper.findAll('.source-bar button')[1].trigger('click')
    expect(wrapper.find('rect.mark').attributes('x')).toBe('28.5')
    await wrapper.findAll('.source-bar button')[0].trigger('click')
    expect(wrapper.find('rect.mark').attributes('x')).toBe('8.5')
  })
  it('preserves questionnaire drafts across initial load, marks and pending submission', async () => {
    const load = deferred<FeedbackState>()
    const sheet = deferred<FeedbackState>()
    vi.spyOn(sharingApi, 'feedback').mockReturnValue(load.promise)
    vi.spyOn(sharingApi, 'saveMark').mockResolvedValue({ ...empty, marks: [
      { episode: 1, ordinal: 0, reaction: 'impressed', slop: false, comment: null },
    ] })
    vi.spyOn(sharingApi, 'saveSheet').mockReturnValue(sheet.promise)
    const wrapper = reader()
    await wrapper.find('.head button').trigger('click')
    await wrapper.find('#share-worked').setValue('Draft')
    await wrapper.find('#share-did-not').setValue('Other draft')
    await wrapper.findAll('.star')[3].trigger('click')
    load.resolve(empty)
    await flushPromises()
    expect((wrapper.find('#share-worked').element as HTMLTextAreaElement).value).toBe('Draft')
    await wrapper.find('.panel__head button').trigger('click')
    await wrapper.find('[aria-label="Beeindruckt"]').trigger('click')
    await flushPromises()
    await wrapper.find('.head button').trigger('click')
    expect(wrapper.find('#share-label').exists()).toBe(false)
    expect((wrapper.find('#share-did-not').element as HTMLTextAreaElement).value).toBe('Other draft')
    await wrapper.find('.panel__foot .btn--primary').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1100)
    expect(sharingApi.saveSheet).toHaveBeenCalledWith('token', {
      label: null, stars: 4, worked: 'Draft', did_not: 'Other draft',
    })
    await wrapper.find('#share-worked').setValue('New draft')
    sheet.resolve({ ...empty, label: 'Reviewer', stars: 4, worked: 'Draft', did_not: 'Other draft' })
    await flushPromises()
    expect((wrapper.find('#share-worked').element as HTMLTextAreaElement).value).toBe('New draft')
    expect(wrapper.find('[aria-label="Beeindruckt"]').attributes('aria-pressed')).toBe('true')
  })
  it.each([false, true])('orders rapid mark then clear, including failed earlier writes (%s)', async fail => {
    vi.spyOn(sharingApi, 'feedback').mockResolvedValue(empty)
    const first = deferred<FeedbackState>()
    const save = vi.spyOn(sharingApi, 'saveMark').mockReturnValueOnce(first.promise).mockResolvedValue(empty)
    const wrapper = reader()
    await flushPromises()
    const button = wrapper.find('[aria-label="Beeindruckt"]')
    await button.trigger('click')
    await button.trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledTimes(1)
    expect(button.attributes('aria-pressed')).toBe('false')
    if (fail) first.reject(new Error('failed'))
    else first.resolve({ ...empty, marks: [{ episode: 1, ordinal: 0, reaction: 'impressed', slop: false, comment: null }] })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1100)
    expect(save).toHaveBeenCalledTimes(2)
    expect(save.mock.calls[1][1].reaction).toBe(null)
    expect(button.attributes('aria-pressed')).toBe('false')
  })
})
