import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import type { DocumentSummary, FolderOut, IngestionReport, StructureOut } from '@/api/types'
import DocumentActions from '@/components/DocumentActions.vue'
import FolderTree from '@/components/FolderTree.vue'
import { useFoldersStore } from '@/stores/folders'
import DocumentDetailView from '@/views/DocumentDetailView.vue'
import DocumentsView from '@/views/DocumentsView.vue'

const SHARED = ['open-bench', 'new-run', 'move', 'rename', 'reparse'] as const

interface Call {
  url: string
  method: string
  body?: string
}

const calls: Call[] = []
const cleanups: Array<() => void> = []
let pinia: Pinia

function documentSummary(overrides: Partial<DocumentSummary> = {}): DocumentSummary {
  return {
    id: 'doc-1',
    filename: 'bericht.pdf',
    sha256: 'abc',
    title: 'Bericht',
    uploaded_at: '2026-01-01T00:00:00Z',
    uploaded_by: null,
    page_count: 3,
    language: 'de',
    parse_version: 1,
    parse_status: 'parsed',
    parse_error: null,
    report: null,
    folder_id: null,
    ...overrides,
  }
}

function folder(): FolderOut {
  return {
    id: 'folder-1',
    name: 'Vertraege',
    parent_id: null,
    depth: 0,
    path: ['Vertraege'],
    document_count: 1,
    total_document_count: 1,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function structure(text = 'Hallo'): StructureOut {
  return {
    document_id: 'doc-1',
    parse_version: 1,
    language: 'de',
    page_count: 1,
    page_sizes: [[595, 842]],
    sections: [],
    blocks: [
      {
        id: 'b1',
        ordinal: 0,
        text,
        page: 0,
        bboxes: [[0, [0, 0, 10, 10]]],
        zone: 'body',
        zone_confidence: 1,
        zone_uncertain: false,
        salience: 1,
        section_id: null,
        heading_level: null,
        is_table: false,
        zone_overridden_from: null,
      },
    ],
    objectives: [],
    zone_catalogue: [],
  }
}

function ingestion(reason: string): IngestionReport {
  return {
    language: 'de',
    language_confidence: 0.9,
    page_count: 3,
    extractable_words: 100,
    narratable_words: 80,
    visual_content_ratio: 0,
    text_density: 200,
    structure_source: 'outline',
    structure_confidence: 'high',
    section_count: 1,
    zone_distribution: { body: 1 },
    zone_uncertain_ratio: 0,
    table_count: 0,
    boilerplate_lines_removed: 0,
    anchor_integrity: 1,
    reading_order_confidence: 1,
    warnings: [],
    ingestion_confidence: 'high',
    confidence_reasons: [reason],
  }
}

interface ParseScript {
  status: DocumentSummary['parse_status']
  parseError: string | null
  parseVersion: number
  reason: string
  blockText: string
}

function listGets(): number {
  return calls.filter((call) => call.method === 'GET' && call.url === '/api/documents').length
}

function installParseScript(script: ParseScript): void {
  calls.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
      if (method === 'POST' && url.endsWith('/reparse')) {
        script.status = 'pending'
        script.parseError = null
        return json(documentSummary({ parse_status: 'pending', parse_error: null }))
      }
      if (url.endsWith('/structure')) return json(structure(script.blockText))
      const summary = documentSummary({
        parse_status: script.status,
        parse_error: script.parseError,
        parse_version: script.parseVersion,
        report: null,
      })
      if (url === '/api/documents') return json([summary])
      if (url === '/api/folders') return json([folder()])
      return json({
        ...summary,
        report: script.status === 'parsed' ? ingestion(script.reason) : null,
      })
    }),
  )
}

async function advancePoll(): Promise<void> {
  await vi.advanceTimersByTimeAsync(2000)
  await settle()
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function installFetch(documents: DocumentSummary[] = [documentSummary()]): void {
  calls.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? init.body : undefined
      calls.push({ url, method, body })
      if (url.endsWith('/structure')) return json(structure())
      if (method === 'PATCH') {
        if (failPatch) return json({ title: 'no', detail: 'zu lang' }, 400)
        const title = JSON.parse(body ?? '{}').title as string
        return json(documentSummary({ title }))
      }
      if (url === '/api/documents') return json(documents)
      if (url === '/api/folders') return json([folder()])
      return json(documents[0] ?? documentSummary())
    }),
  )
}

let failPatch = false

async function routerAt(path: string): Promise<Router> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/documents', name: 'documents', component: { template: '<div />' } },
      { path: '/documents/:id', name: 'document', component: { template: '<div />' } },
      { path: '/documents/:id/runs/new', name: 'new-run', component: { template: '<div />' } },
      { path: '/experiments/bench', name: 'bench', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  return router
}

async function mountActions(variant: 'menu' | 'bar', summary = documentSummary()) {
  const wrapper = mount(DocumentActions, {
    props: { document: summary, variant },
    attachTo: document.body,
    global: { plugins: [pinia, await routerAt('/documents')] },
  })
  cleanups.push(() => wrapper.unmount())
  return wrapper
}

async function settle(): Promise<void> {
  for (let i = 0; i < 6; i += 1) await flushPromises()
}

function actionIds(root: ParentNode): string[] {
  return [...root.querySelectorAll<HTMLElement>('[data-action]')].map(
    (element) => element.dataset.action ?? '',
  )
}

function setControlValue(element: HTMLInputElement | HTMLSelectElement, value: string): void {
  const prototype = Object.getPrototypeOf(element) as object
  const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value')
  descriptor?.set?.call(element, value)
  const eventName = element instanceof HTMLSelectElement ? 'change' : 'input'
  element.dispatchEvent(new Event(eventName, { bubbles: true }))
}

beforeEach(() => {
  failPatch = false
  pinia = createPinia()
  setActivePinia(pinia)
  installFetch()
})

afterEach(() => {
  for (const cleanup of cleanups) cleanup()
  cleanups.length = 0
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('DocumentActions', () => {
  it('offers the same actions from the card menu and the detail bar', async () => {
    const menu = await mountActions('menu')
    await menu.get('button[aria-haspopup="menu"]').trigger('click')
    await nextTick()

    const bar = await mountActions('bar')

    expect(actionIds(menu.element)).toEqual(['open-detail', ...SHARED])
    expect(actionIds(bar.element)).toEqual([...SHARED])
    expect(menu.text()).toContain('Struktur ansehen')
    expect(menu.text()).toContain('In der Werkbank öffnen')
    expect(menu.text()).toContain('Lauf starten')
    expect(menu.text()).toContain('Ordner')
    expect(menu.text()).toContain('Umbenennen')
    expect(menu.text()).toContain('Neu einlesen')
    expect(bar.text()).toContain('In der Werkbank öffnen')
    expect(bar.text()).toContain('Neu einlesen')
    expect(bar.text()).not.toContain('Struktur ansehen')
  })

  it('hides run and workbench actions until the document is parsed', async () => {
    const summary = documentSummary({ parse_status: 'failed' })
    const menu = await mountActions('menu', summary)
    await menu.get('button[aria-haspopup="menu"]').trigger('click')
    await nextTick()
    const bar = await mountActions('bar', summary)

    expect(actionIds(menu.element)).toEqual(['open-detail', 'move', 'rename', 'reparse'])
    expect(actionIds(bar.element)).toEqual(['move', 'rename', 'reparse'])
  })

  it('moves the menu with the keyboard and closes on escape', async () => {
    const wrapper = await mountActions('menu')
    const button = wrapper.get('button[aria-haspopup="menu"]')
    await button.trigger('keydown', { key: 'ArrowDown' })
    await nextTick()

    expect(document.activeElement).toBe(wrapper.get('[data-action="open-detail"]').element)
    await wrapper.get('[role="menu"]').trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(wrapper.get('[data-action="open-bench"]').element)

    await wrapper.get('[role="menu"]').trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    expect(document.activeElement).toBe(button.element)

    await button.trigger('keydown', { key: 'ArrowUp' })
    await nextTick()
    expect(document.activeElement).toBe(wrapper.get('[data-action="reparse"]').element)
  })

  it('renames through the same dialog the card already used', async () => {
    const wrapper = await mountActions('menu')
    await wrapper.get('button[aria-haspopup="menu"]').trigger('click')
    await nextTick()
    await wrapper.get('[data-action="rename"]').trigger('click')
    await nextTick()

    expect(document.body.textContent).toContain('Nur der angezeigte Name ändert sich')
    const input = document.body.querySelector('input') as HTMLInputElement
    setControlValue(input, 'Neuer Name')
    document.body.querySelector<HTMLButtonElement>('[data-action="confirm"]')?.click()
    await settle()

    const patch = calls.find((call) => call.method === 'PATCH')
    expect(JSON.parse(patch?.body ?? '{}')).toEqual({ title: 'Neuer Name' })
    expect(wrapper.get('[role="status"]').text()).toContain('Dokument umbenannt.')
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })

  it('keeps a rename failure inside the dialog', async () => {
    failPatch = true
    const wrapper = await mountActions('bar')
    await wrapper.get('[data-action="rename"]').trigger('click')
    await nextTick()
    const input = document.body.querySelector('input') as HTMLInputElement
    setControlValue(input, 'Neuer Name')
    document.body.querySelector<HTMLButtonElement>('[data-action="confirm"]')?.click()
    await settle()

    expect(document.body.textContent).toContain('zu lang')
    expect(wrapper.emitted('changed')).toBeUndefined()
  })

  it('files a document from the folder picker, including back to no folder', async () => {
    useFoldersStore().items = [folder()]
    const wrapper = await mountActions('bar', documentSummary({ folder_id: 'folder-1' }))
    await wrapper.get('[data-action="move"]').trigger('click')
    await nextTick()

    const select = document.body.querySelector('select') as HTMLSelectElement
    expect(select.textContent).toContain('Ohne Ordner')
    expect(select.textContent).toContain('Vertraege')
    select.selectedIndex = 0
    select.dispatchEvent(new Event('change', { bubbles: true }))
    document.body.querySelector<HTMLButtonElement>('[data-action="confirm"]')?.click()
    await settle()

    const move = calls.find((call) => call.url.endsWith('/move'))
    expect(JSON.parse(move?.body ?? '{}')).toEqual({ folder_id: null })
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })

  it('queues a new read-in', async () => {
    const wrapper = await mountActions('bar')
    await wrapper.get('[data-action="reparse"]').trigger('click')
    await settle()

    expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/reparse'))).toBe(true)
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })
})

describe('document screens', () => {
  it('keeps drag-onto-folder and shows the full menu on the overview card', async () => {
    const router = await routerAt('/documents')
    const wrapper = mount(DocumentsView, {
      attachTo: document.body,
      global: { plugins: [pinia, router] },
    })
    cleanups.push(() => wrapper.unmount())
    await settle()

    expect(wrapper.get('.doc').attributes('draggable')).toBe('true')
    wrapper.getComponent(FolderTree).vm.$emit('drop-document', {
      documentId: 'doc-1',
      folderId: 'folder-1',
    })
    await settle()
    const move = calls.find((call) => call.url.endsWith('/move'))
    expect(JSON.parse(move?.body ?? '{}')).toEqual({ folder_id: 'folder-1' })

    await wrapper.get('button[aria-haspopup="menu"]').trigger('click')
    await nextTick()
    expect(actionIds(wrapper.element)).toEqual(['open-detail', ...SHARED])
  })

  it('shows the same actions on the structure view and still relabels a block', async () => {
    const router = await routerAt('/documents/doc-1')
    const wrapper = mount(DocumentDetailView, {
      props: { id: 'doc-1' },
      attachTo: document.body,
      global: { plugins: [pinia, router] },
    })
    cleanups.push(() => wrapper.unmount())
    await settle()

    expect(actionIds(wrapper.element)).toEqual([...SHARED])
    expect(wrapper.text()).toContain('Textbereich ändern')
    expect(wrapper.find('button[aria-haspopup="menu"]').exists()).toBe(false)
  })

  it('follows a read-in started on the structure view until it is parsed', async () => {
    const script: ParseScript = {
      status: 'parsed',
      parseError: null,
      parseVersion: 1,
      reason: 'erster Grund',
      blockText: 'Hallo',
    }
    installParseScript(script)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      const router = await routerAt('/documents/doc-1')
      const wrapper = mount(DocumentDetailView, {
        props: { id: 'doc-1' },
        attachTo: document.body,
        global: { plugins: [pinia, router] },
      })
      cleanups.push(() => wrapper.unmount())
      await settle()

      expect(wrapper.text()).toContain('Hallo')
      expect(wrapper.text()).toContain('erster Grund')
      expect(wrapper.text()).toContain('In der Werkbank öffnen')

      await wrapper.get('[data-action="reparse"]').trigger('click')
      await settle()

      expect(wrapper.text()).toContain('Wartet')
      expect(wrapper.text()).not.toContain('Hallo')
      expect(wrapper.text()).not.toContain('In der Werkbank öffnen')
      expect(wrapper.text()).not.toContain('Lauf starten')
      const structuresWhilePending = calls.filter((call) => call.url.endsWith('/structure')).length

      const listsAtPending = listGets()
      await vi.advanceTimersByTimeAsync(1999)
      await settle()
      expect(listGets()).toBe(listsAtPending)

      script.status = 'parsing'
      await vi.advanceTimersByTimeAsync(1)
      await settle()
      expect(listGets()).toBe(listsAtPending + 1)
      expect(wrapper.text()).toContain('Wird eingelesen')
      expect(wrapper.find('[data-action="open-bench"]').exists()).toBe(false)
      expect(calls.filter((call) => call.url.endsWith('/structure')).length).toBe(
        structuresWhilePending,
      )

      script.status = 'parsed'
      script.parseVersion = 2
      script.reason = 'zweiter Grund'
      script.blockText = 'Neu gelesen'
      await advancePoll()

      expect(wrapper.text()).toContain('Neu gelesen')
      expect(wrapper.text()).toContain('zweiter Grund')
      expect(wrapper.text()).toContain('In der Werkbank öffnen')
      expect(wrapper.text()).toContain('Lauf starten')
      expect(wrapper.text()).not.toContain('Wartet')
      expect(calls.filter((call) => call.url.endsWith('/structure')).length).toBe(
        structuresWhilePending + 1,
      )
      expect(calls.some((call) => call.url === '/api/documents' && call.method === 'GET')).toBe(true)
      const detail = calls.filter((call) => call.url === '/api/documents/doc-1')
      expect(detail.length).toBeGreaterThan(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('follows a read-in started on the structure view until it fails', async () => {
    const script: ParseScript = {
      status: 'parsed',
      parseError: null,
      parseVersion: 1,
      reason: 'erster Grund',
      blockText: 'Hallo',
    }
    installParseScript(script)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      const router = await routerAt('/documents/doc-1')
      const wrapper = mount(DocumentDetailView, {
        props: { id: 'doc-1' },
        attachTo: document.body,
        global: { plugins: [pinia, router] },
      })
      cleanups.push(() => wrapper.unmount())
      await settle()

      await wrapper.get('[data-action="reparse"]').trigger('click')
      await settle()
      const structuresWhilePending = calls.filter((call) => call.url.endsWith('/structure')).length

      script.status = 'failed'
      script.parseError = 'Seite leer\nTraceback (most recent call last)'
      await advancePoll()

      expect(wrapper.text()).toContain('Fehlgeschlagen')
      expect(wrapper.text()).toContain('Seite leer')
      expect(wrapper.text()).not.toContain('Traceback')
      expect(wrapper.text()).not.toContain('Hallo')
      expect(wrapper.find('[data-action="open-bench"]').exists()).toBe(false)
      expect(wrapper.find('[data-action="new-run"]').exists()).toBe(false)
      expect(calls.filter((call) => call.url.endsWith('/structure')).length).toBe(
        structuresWhilePending,
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('follows an in-progress read-in when the structure view opens', async () => {
    const script: ParseScript = {
      status: 'pending',
      parseError: null,
      parseVersion: 1,
      reason: 'danach',
      blockText: 'Fertig gelesen',
    }
    installParseScript(script)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      const router = await routerAt('/documents/doc-1')
      const wrapper = mount(DocumentDetailView, {
        props: { id: 'doc-1' },
        attachTo: document.body,
        global: { plugins: [pinia, router] },
      })
      cleanups.push(() => wrapper.unmount())
      await settle()

      expect(wrapper.text()).toContain('Wartet')
      expect(wrapper.text()).not.toContain('Fertig gelesen')
      expect(wrapper.find('[data-action="open-bench"]').exists()).toBe(false)
      expect(listGets()).toBe(1)

      script.status = 'parsing'
      await advancePoll()
      expect(wrapper.text()).toContain('Wird eingelesen')

      script.status = 'parsed'
      script.parseVersion = 2
      await advancePoll()
      expect(wrapper.text()).toContain('Fertig gelesen')
      expect(wrapper.text()).toContain('danach')
      expect(wrapper.text()).toContain('In der Werkbank öffnen')
    } finally {
      vi.useRealTimers()
    }
  })

  it('stops following a read-in when the structure view closes', async () => {
    const script: ParseScript = {
      status: 'parsed',
      parseError: null,
      parseVersion: 1,
      reason: 'erster Grund',
      blockText: 'Hallo',
    }
    installParseScript(script)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      const router = await routerAt('/documents/doc-1')
      const wrapper = mount(DocumentDetailView, {
        props: { id: 'doc-1' },
        attachTo: document.body,
        global: { plugins: [pinia, router] },
      })
      cleanups.push(() => wrapper.unmount())
      await settle()
      await wrapper.get('[data-action="reparse"]').trigger('click')
      await settle()
      expect(wrapper.text()).toContain('Wartet')

      const lists = listGets()
      wrapper.unmount()
      await vi.advanceTimersByTimeAsync(5000)
      await settle()
      expect(listGets()).toBe(lists)
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not resume polling after the structure view closes during a list reload', async () => {
    let releaseList = (): void => {}
    let holdList = false
    let status: DocumentSummary['parse_status'] = 'parsed'
    calls.length = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        const method = init?.method ?? 'GET'
        calls.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
        if (method === 'POST' && url.endsWith('/reparse')) {
          status = 'pending'
          holdList = true
          return json(documentSummary({ parse_status: 'pending', parse_error: null, report: null }))
        }
        if (url.endsWith('/structure')) return json(structure())
        if (url === '/api/documents' && holdList) {
          holdList = false
          await new Promise<void>((resolve) => {
            releaseList = resolve
          })
        }
        const summary = documentSummary({
          parse_status: status,
          parse_error: null,
          report: status === 'parsed' ? ingestion('erster Grund') : null,
        })
        if (url === '/api/documents') return json([summary])
        return json(summary)
      }),
    )
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      const router = await routerAt('/documents/doc-1')
      const wrapper = mount(DocumentDetailView, {
        props: { id: 'doc-1' },
        attachTo: document.body,
        global: { plugins: [pinia, router] },
      })
      cleanups.push(() => wrapper.unmount())
      await settle()

      const pendingClick = wrapper.get('[data-action="reparse"]').trigger('click')
      await flushPromises()
      expect(listGets()).toBe(1)

      wrapper.unmount()
      releaseList()
      await pendingClick
      await settle()
      const lists = listGets()
      await vi.advanceTimersByTimeAsync(5000)
      await settle()
      expect(listGets()).toBe(lists)
    } finally {
      releaseList()
      vi.useRealTimers()
    }
  })
})
