<script setup lang="ts">
/**
 * Every document-level action, shared by the overview card and the structure view.
 *
 * The card uses a compact menu; the structure view uses a bar. Both run the same
 * operations: open the structure (menu only — the bar is already that screen),
 * open the workbench, start a run, file into a folder, rename, and re-read.
 * Relabel stays on the structure view because it edits one block, not the document.
 * Dragging a card onto a folder stays on the overview.
 */
import { computed, nextTick, onMounted, onUnmounted, ref, useId } from 'vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'

import type { DocumentSummary } from '@/api/types'
import ModalDialog from '@/components/ModalDialog.vue'
import { t } from '@/i18n'
import { useDocumentsStore } from '@/stores/documents'
import { useFoldersStore } from '@/stores/folders'

const props = defineProps<{
  document: DocumentSummary
  /** `menu` is the card control; `bar` is the structure-view row. */
  variant: 'menu' | 'bar'
}>()

const emit = defineEmits<{ (event: 'changed'): void }>()

const documents = useDocumentsStore()
const folders = useFoldersStore()

const rootEl = ref<HTMLElement | null>(null)
const menuButton = ref<HTMLButtonElement | null>(null)
const menuEl = ref<HTMLElement | null>(null)
const menuOpen = ref(false)
const menuId = useId()
const nameFieldId = useId()
const folderFieldId = useId()

const dialog = ref<'rename' | 'move' | null>(null)
const draftName = ref('')
const draftFolder = ref<string | null>(null)
const busy = ref(false)
const error = ref('')
const notice = ref('')
let noticeTimer: number | undefined

interface ActionItem {
  id: string
  label: string
  visible: boolean
  tone: 'default' | 'mark' | 'ghost'
  to?: RouteLocationRaw
  run?: () => void | Promise<void>
}

const parsed = computed(() => props.document.parse_status === 'parsed')

const actions = computed<ActionItem[]>(() => [
  {
    id: 'open-detail',
    label: t.documents.openDetail,
    visible: props.variant === 'menu',
    tone: 'default',
    to: { name: 'document', params: { id: props.document.id } },
  },
  {
    id: 'open-bench',
    label: t.documents.openBench,
    visible: parsed.value,
    tone: 'default',
    to: { name: 'bench', query: { document: props.document.id } },
  },
  {
    id: 'new-run',
    label: t.documents.newRun,
    visible: parsed.value,
    tone: 'mark',
    to: { name: 'new-run', params: { id: props.document.id } },
  },
  {
    id: 'move',
    label: t.folders.inFolder,
    visible: true,
    tone: 'ghost',
    run: openMove,
  },
  {
    id: 'rename',
    label: t.common.rename,
    visible: true,
    tone: 'ghost',
    run: openRename,
  },
  {
    id: 'reparse',
    label: t.documents.reparse,
    visible: true,
    tone: 'ghost',
    run: reparse,
  },
])

const visibleActions = computed(() => actions.value.filter((action) => action.visible))

function barClass(tone: ActionItem['tone']): string[] {
  if (tone === 'mark') return ['btn', 'btn--mark']
  if (tone === 'ghost') return ['btn', 'btn--ghost']
  return ['btn']
}

function menuItems(): HTMLElement[] {
  return Array.from(menuEl.value?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])
}

function closeMenu(): void {
  menuOpen.value = false
}

async function openMenu(focus: 'first' | 'last'): Promise<void> {
  menuOpen.value = true
  await nextTick()
  const items = menuItems()
  if (!items.length) return
  const target = focus === 'first' ? items[0] : items[items.length - 1]
  target.focus()
}

function toggleMenu(): void {
  if (menuOpen.value) {
    closeMenu()
    return
  }
  void openMenu('first')
}

function onButtonKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    void openMenu('first')
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    void openMenu('last')
  }
}

function onMenuKeydown(event: KeyboardEvent): void {
  const items = menuItems()
  if (!items.length) return
  const current = items.indexOf(document.activeElement as HTMLElement)
  if (event.key === 'Escape') {
    event.preventDefault()
    closeMenu()
    menuButton.value?.focus()
    return
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
  }
  if (event.key === 'Home') {
    items[0]?.focus()
    return
  }
  if (event.key === 'End') {
    items[items.length - 1]?.focus()
    return
  }
  if (event.key === 'ArrowDown') {
    const next = current < 0 ? 0 : (current + 1) % items.length
    items[next]?.focus()
    return
  }
  if (event.key === 'ArrowUp') {
    const next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length
    items[next]?.focus()
    return
  }
  if (event.key === 'Tab') closeMenu()
}

function onPointerDown(event: Event): void {
  if (!menuOpen.value) return
  const target = event.target
  if (target instanceof Node && rootEl.value?.contains(target)) return
  closeMenu()
}

function onAction(action: ActionItem): void {
  if (busy.value) return
  closeMenu()
  void action.run?.()
}

function announce(message: string): void {
  notice.value = message
  window.clearTimeout(noticeTimer)
  noticeTimer = window.setTimeout(() => (notice.value = ''), 4000)
}

function closeDialog(): void {
  dialog.value = null
  error.value = ''
}

function openRename(): void {
  draftName.value = props.document.title || props.document.filename
  error.value = ''
  dialog.value = 'rename'
}

async function openMove(): Promise<void> {
  error.value = ''
  draftFolder.value = props.document.folder_id
  try {
    if (!folders.items.length) await folders.load()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : t.errors.generic
  }
  dialog.value = 'move'
}

async function confirmRename(): Promise<void> {
  const title = draftName.value.trim()
  if (!title || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await documents.rename(props.document.id, title)
    dialog.value = null
    announce(t.documents.renamed)
    emit('changed')
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : t.documents.renameFailed
  } finally {
    busy.value = false
  }
}

async function confirmMove(): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await documents.move(props.document.id, draftFolder.value)
    await folders.load()
    dialog.value = null
    emit('changed')
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : t.errors.generic
  } finally {
    busy.value = false
  }
}

async function reparse(): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await documents.reparse(props.document.id)
    emit('changed')
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : t.errors.generic
  } finally {
    busy.value = false
  }
}

onMounted(() => document.addEventListener('pointerdown', onPointerDown))
onUnmounted(() => {
  document.removeEventListener('pointerdown', onPointerDown)
  window.clearTimeout(noticeTimer)
})
</script>

<template>
  <div
    ref="rootEl"
    class="doc-actions"
    :class="[`doc-actions--${variant}`, { 'doc-actions--open': menuOpen }]"
  >
    <template v-if="variant === 'menu'">
      <button
        ref="menuButton"
        type="button"
        class="btn btn--sm"
        aria-haspopup="menu"
        :aria-expanded="menuOpen"
        :aria-controls="menuId"
        :aria-label="`${t.documents.actions}: ${document.title || document.filename}`"
        :disabled="busy"
        @click.stop="toggleMenu"
        @keydown="onButtonKeydown"
      >
        {{ t.documents.actions }}
      </button>
      <ul
        v-if="menuOpen"
        :id="menuId"
        ref="menuEl"
        class="doc-actions__menu"
        role="menu"
        :aria-label="t.documents.actions"
        @keydown="onMenuKeydown"
      >
        <li v-for="action in visibleActions" :key="action.id" role="none">
          <RouterLink
            v-if="action.to"
            class="doc-actions__item"
            role="menuitem"
            tabindex="-1"
            :data-action="action.id"
            :to="action.to"
            @click="onAction(action)"
          >
            {{ action.label }}
          </RouterLink>
          <button
            v-else
            type="button"
            class="doc-actions__item"
            role="menuitem"
            tabindex="-1"
            :data-action="action.id"
            :disabled="busy"
            @click="onAction(action)"
          >
            {{ action.label }}
          </button>
        </li>
      </ul>
    </template>

    <div v-else class="doc-actions__bar">
      <template v-for="action in visibleActions" :key="action.id">
        <RouterLink
          v-if="action.to"
          :class="barClass(action.tone)"
          :data-action="action.id"
          :to="action.to"
        >
          {{ action.label }}
        </RouterLink>
        <button
          v-else
          type="button"
          :class="barClass(action.tone)"
          :data-action="action.id"
          :disabled="busy"
          @click="onAction(action)"
        >
          {{ action.label }}
        </button>
      </template>
    </div>

    <p v-if="notice" class="doc-actions__notice" role="status">{{ notice }}</p>
    <p v-if="error && !dialog" class="doc-actions__error" role="alert">{{ error }}</p>

    <ModalDialog
      :open="dialog !== null"
      :title="dialog === 'rename' ? t.documents.renameTitle : t.folders.moveDocument"
      :lead="dialog === 'rename' ? t.documents.renameLead : t.folders.moveDocumentLead"
      @close="closeDialog"
    >
      <div class="stack">
        <div v-if="dialog === 'rename'" class="field">
          <label :for="nameFieldId">{{ t.documents.nameLabel }}</label>
          <input
            :id="nameFieldId"
            v-model="draftName"
            class="input"
            maxlength="200"
            data-autofocus
            @keydown.enter.prevent="confirmRename"
          />
          <p class="meta">{{ t.documents.nameMax }}</p>
        </div>

        <div v-if="dialog === 'move'" class="field">
          <label :for="folderFieldId">{{ t.folders.title }}</label>
          <select :id="folderFieldId" v-model="draftFolder" class="select" data-autofocus>
            <option :value="null">{{ t.folders.root }}</option>
            <option v-for="folder in folders.items" :key="folder.id" :value="folder.id">
              {{ folder.path.join(' / ') }}
            </option>
          </select>
        </div>

        <p v-if="error" class="doc-actions__error" role="alert">{{ error }}</p>
      </div>

      <template #actions>
        <button type="button" class="btn" @click="closeDialog">{{ t.common.cancel }}</button>
        <button
          type="button"
          class="btn btn--mark"
          data-action="confirm"
          :disabled="busy || (dialog === 'rename' && !draftName.trim())"
          @click="dialog === 'rename' ? confirmRename() : confirmMove()"
        >
          {{ t.common.save }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.doc-actions {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--s2);
}

.doc-actions--menu {
  align-items: flex-start;
}

.doc-actions--bar {
  align-items: flex-end;
}

.doc-actions__menu {
  position: absolute;
  z-index: 5;
  top: calc(100% + 4px);
  left: 0;
  min-width: 14rem;
  margin: 0;
  padding: var(--s2);
  list-style: none;
  background: var(--card);
  border: 1px solid var(--rule-strong);
  border-radius: var(--r-md);
  box-shadow: var(--shadow-md);
}

.doc-actions__item {
  display: block;
  width: 100%;
  padding: 6px 10px;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: var(--t-sm);
  text-align: left;
  text-decoration: none;
  cursor: pointer;
}

.doc-actions__item:hover,
.doc-actions__item:focus-visible {
  background: var(--chrome);
  color: var(--ink);
}

.doc-actions__item:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.doc-actions__bar {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: var(--s2);
}

.doc-actions a {
  text-decoration: none;
}

.doc-actions__notice {
  margin: 0;
  padding: var(--s2) var(--s3);
  font-size: var(--t-sm);
  color: var(--pass);
  background: var(--pass-soft);
  border-radius: var(--r-sm);
}

.doc-actions__error {
  margin: 0;
  font-size: var(--t-sm);
  color: var(--fail);
}
</style>
