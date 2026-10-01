import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import type { DocumentSummary, FolderOut, StructureOut } from '@/api/types'
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

function structure(): StructureOut {
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
        text: 'Hallo',
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
})
