import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'

import DataBundleButton from './DataBundleButton.vue'

function zipResponse(): Response {
  return new Response(new Blob(['PK']), {
    status: 200,
    headers: {
      'content-type': 'application/zip',
      'content-disposition': 'attachment; filename="kalliope-lauf-r1-daten.zip"',
    },
  })
}

describe('data bundle button', () => {
  const { createObjectURL, revokeObjectURL } = URL
  let saved: string[]

  beforeEach(() => {
    saved = []
    URL.createObjectURL = vi.fn(() => 'blob:bundle')
    URL.revokeObjectURL = vi.fn()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      saved.push(this.download)
    })
  })

  afterEach(() => {
    URL.createObjectURL = createObjectURL
    URL.revokeObjectURL = revokeObjectURL
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('shows that it is building the file, then saves it under the server name', async () => {
    let finish: (response: Response) => void = () => {}
    const fetch = vi.fn(() => new Promise<Response>((resolve) => (finish = resolve)))
    vi.stubGlobal('fetch', fetch)
    const wrapper = mount(DataBundleButton, {
      props: { path: '/api/runs/r1/bundle', hint: t.graph.bundleHint },
    })
    const button = wrapper.get('[data-bundle]')
    expect(button.text()).toBe(t.graph.bundle)
    expect(button.attributes('title')).toBe(t.graph.bundleHint)

    await button.trigger('click')
    expect(fetch).toHaveBeenCalledWith('/api/runs/r1/bundle', { credentials: 'same-origin' })
    expect(button.text()).toBe(t.graph.bundleBuilding)
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.attributes('aria-busy')).toBe('true')
    expect(wrapper.find('.spinner').exists()).toBe(true)

    // A second click while it builds does not start another download.
    await button.trigger('click')
    expect(fetch).toHaveBeenCalledTimes(1)

    finish(zipResponse())
    await flushPromises()
    expect(saved).toEqual(['kalliope-lauf-r1-daten.zip'])
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:bundle')
    expect(button.text()).toBe(t.graph.bundle)
    expect(button.attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.spinner').exists()).toBe(false)
  })

  it('says why the server refused', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ title: 'Run is in the trash', detail: 'Restore the run first.' }), {
          status: 409,
          headers: { 'content-type': 'application/problem+json' },
        }),
      ),
    )
    const wrapper = mount(DataBundleButton, {
      props: { path: '/api/runs/r1/bundle', hint: t.graph.bundleHint },
    })
    await wrapper.get('[data-bundle]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('Restore the run first.')
    expect(saved).toEqual([])
    expect(wrapper.get('[data-bundle]').attributes('disabled')).toBeUndefined()
  })

  it('stays disabled while the run is in the trash', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const wrapper = mount(DataBundleButton, {
      props: { path: '/api/runs/r1/bundle', hint: t.graph.bundleHint, trashed: true },
    })
    const button = wrapper.get('[data-bundle]')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.attributes('title')).toBe(t.graph.bundleTrashed)
    await button.trigger('click')
    expect(fetch).not.toHaveBeenCalled()
  })
})
