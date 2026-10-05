import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SharedSnapshot } from '@/api/sharing'
import SharedContent from './SharedContent.vue'

const snapshot: SharedSnapshot = {
  title: 'Frozen recording', created_at: '2026-10-01', expires_at: '2026-11-01',
  episodes: [{
    index: 1, title: 'Episode', audio: { url: '/recording', duration_s: 45 },
    pages: [
      { page: 2, width: 600, height: 1000, url: '/frozen-page-2.png' },
      { page: 5, width: 600, height: 1000, url: '/frozen-page-5.png' },
    ],
    segments: [
      { ordinal: 0, speaker: 'A', text: 'First', start_s: 1.2, end_s: 4.8, citations: [{ rects: [{ page: 2, bbox: [10, 300, 100, 350] }] }] },
      { ordinal: 1, speaker: 'B', text: 'Second', start_s: 9.7, end_s: 18.1, citations: [
        { rects: [{ page: 5, bbox: [10, 700, 100, 750] }] },
        { rects: [{ page: 2, bbox: [10, 50, 100, 100] }] },
      ] },
      { ordinal: 2, speaker: 'A', text: 'Uncited', start_s: 18.1, end_s: 25.2 },
      { ordinal: 3, speaker: 'B', text: 'Missing page', start_s: 25.2, end_s: 31.8, citations: [{ rects: [{ page: 99, bbox: [10, 700, 100, 750] }] }] },
      { ordinal: 4, speaker: 'A', text: 'No timing', start_s: null, end_s: null },
    ],
  }, { index: 2, title: 'No alignment', segments: [{ speaker: 'A', text: 'Still readable' }], audio: { url: '/other', duration_s: 10 } }],
}
let wrapper: ReturnType<typeof mount>
function rect(top: number, height: number): DOMRect {
  return { top, bottom: top + height, height, width: 600, left: 0, right: 600, x: 0, y: top, toJSON: () => ({}) }
}
beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  wrapper = mount(SharedContent, { props: { snapshot, selected: 1 } })
  const script = wrapper.find('.script').element as HTMLElement
  const source = wrapper.find('.source').element as HTMLElement
  Object.defineProperties(script, { clientHeight: { value: 400 }, scrollHeight: { value: 1800, configurable: true } })
  Object.defineProperties(source, { clientHeight: { value: 400 }, scrollHeight: { value: 1200 } })
  vi.spyOn(script, 'getBoundingClientRect').mockImplementation(() => rect(100, 400))
  vi.spyOn(source, 'getBoundingClientRect').mockImplementation(() => rect(100, 400))
  wrapper.findAll('.segment').forEach((line, index) => {
    vi.spyOn(line.element, 'getBoundingClientRect').mockImplementation(() => rect(100 + index * 400 - script.scrollTop, 200))
  })
  vi.spyOn(wrapper.find('.source-bar').element, 'getBoundingClientRect').mockImplementation(() => rect(100, 40))
  vi.spyOn(SVGElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: SVGElement) {
    return rect(100 + Number(this.getAttribute('y') ?? 0) - source.scrollTop, Number(this.getAttribute('height') ?? 0))
  })
})
afterEach(() => { wrapper.unmount(); vi.restoreAllMocks() })
async function clock(time: number, event = 'timeupdate') {
  const audio = wrapper.find('audio')
  ;(audio.element as HTMLAudioElement).currentTime = time
  await audio.trigger(event)
  await flushPromises()
}
describe('recorded playback follow', () => {
  it('centers on segment changes, clamps edges, and does not fight scrolling on each tick', async () => {
    const script = wrapper.find('.script').element as HTMLElement
    await clock(2, 'play')
    expect(wrapper.find('.segment[aria-current="true"]').text()).toContain('First')
    expect(script.scrollTop).toBe(0)
    await clock(10)
    expect(script.scrollTop).toBe(300)
    script.scrollTop = 425
    await clock(11)
    expect(script.scrollTop).toBe(425)
    await clock(12, 'seeked')
    expect(script.scrollTop).toBe(300)
    await clock(26, 'seeking')
    expect(script.scrollTop).toBe(1100)
    await clock(3, 'seeked')
    expect(script.scrollTop).toBe(0)
    Object.defineProperty(script, 'scrollHeight', { value: 900 })
    await clock(26, 'seeked')
    expect(script.scrollTop).toBe(500)
  })
  it('selects the first retained evidence page and scrolls to its real SVG anchor', async () => {
    await clock(10, 'play')
    expect(wrapper.find('.source img').attributes('src')).toBe('/frozen-page-5.png')
    expect(wrapper.findAll('.segment')[1].findAll('.cites button')[0].attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('.source .mark').attributes('y')).toBe('698.5')
    expect((wrapper.find('.source').element as HTMLElement).scrollTop).toBeCloseTo(505)
    await clock(10, 'pause')
    await wrapper.findAll('.segment')[1].findAll('.cites button')[1].trigger('click')
    await flushPromises()
    expect(wrapper.find('.source img').attributes('src')).toBe('/frozen-page-2.png')
    await clock(10)
    expect(wrapper.find('.source img').attributes('src')).toBe('/frozen-page-2.png')
    await clock(10, 'play')
    expect(wrapper.find('.source img').attributes('src')).toBe('/frozen-page-5.png')
  })
  it('does not fabricate evidence or alignment for uncited, missing-page or untimed content; resets episodes', async () => {
    await clock(10, 'play')
    await clock(20)
    expect(wrapper.find('.source .mark').exists()).toBe(false)
    await clock(26)
    expect(wrapper.find('.source .mark').exists()).toBe(false)
    expect(wrapper.find('.source img').attributes('src')).not.toContain('99')
    await clock(40)
    expect(wrapper.find('.segment--playing').exists()).toBe(false)
    await wrapper.setProps({ selected: 2 })
    await flushPromises()
    await clock(5, 'play')
    expect(wrapper.find('.segment--playing').exists()).toBe(false)
    expect(wrapper.find('.source img').exists()).toBe(false)
    expect(wrapper.text()).toContain('Still readable')
  })
})
