<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import type { DocumentSummary, FolderOut } from '@/api/types'
import ConfidenceStrip from '@/components/ConfidenceStrip.vue'
import FolderTree from '@/components/FolderTree.vue'
import ModalDialog from '@/components/ModalDialog.vue'
import StatusPill from '@/components/StatusPill.vue'
import { t } from '@/i18n'
import { useDocumentsStore } from '@/stores/documents'
import { ROOT, useFoldersStore } from '@/stores/folders'

const documents = useDocumentsStore()
const folders = useFoldersStore()
const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const error = ref('')

/** `null` shows everything, `'root'` the unfiled documents, else a folder id. */
const selected = ref<string | null>(null)
const dragging = ref(false)
const busy = ref(false)

type Prompt =
  | { kind: 'create'; parentId: string | null }
  | { kind: 'rename'; folder: FolderOut }
  | { kind: 'move'; folder: FolderOut }
  | { kind: 'remove'; folder: FolderOut }
  | { kind: 'file'; document: DocumentSummary }

const prompt = ref<Prompt | null>(null)
const draftName = ref('')
const draftParent = ref<string | null>(null)

const visible = computed(() => {
  if (selected.value === null) return documents.items
  if (selected.value === ROOT) return documents.items.filter((d) => d.folder_id === null)
  return documents.items.filter((d) => d.folder_id === selected.value)
})

const rootCount = computed(() => documents.items.filter((d) => d.folder_id === null).length)

const breadcrumb = computed(() =>
  selected.value && selected.value !== ROOT ? folders.pathOf(selected.value) : [],
)

/** Folders a subject may move into: everything except itself and its subtree. */
const destinations = computed(() => {
  const subject = prompt.value?.kind === 'move' ? prompt.value.folder : null
  if (!subject) return folders.items
  const blocked = new Set([subject.id, ...folders.descendants(subject.id)])
  return folders.items.filter((folder) => !blocked.has(folder.id))
})

const promptTitle = computed(() => {
  switch (prompt.value?.kind) {
    case 'create':
      return t.folders.newTitle
    case 'rename':
      return t.folders.renameTitle
    case 'move':
      return t.folders.moveTitle
    case 'remove':
      return t.folders.deleteTitle
    case 'file':
      return t.folders.moveDocument
    default:
      return ''
  }
})

async function pick(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  uploading.value = true
  error.value = ''
  try {
    // Upload straight into whichever folder is open, so filing is not a
    // separate step the reviewer has to remember afterwards.
    const target = selected.value && selected.value !== ROOT ? selected.value : null
    await documents.upload(file, target)
    await folders.load()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : t.errors.generic
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

function startDrag(event: DragEvent, document: DocumentSummary): void {
  event.dataTransfer?.setData('text/kalliope-document', document.id)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
  dragging.value = true
}

async function fileDocument(documentId: string, folderId: string | null): Promise<void> {
  error.value = ''
  try {
    await documents.move(documentId, folderId)
    await folders.load()
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : t.errors.generic
  }
}

function openPrompt(next: Prompt): void {
  prompt.value = next
  draftName.value = next.kind === 'rename' ? next.folder.name : ''
  draftParent.value =
    next.kind === 'create'
      ? next.parentId
      : next.kind === 'move'
        ? next.folder.parent_id
        : next.kind === 'file'
          ? next.document.folder_id
          : null
}

async function confirmPrompt(): Promise<void> {
  const current = prompt.value
  if (!current) return
  busy.value = true
  error.value = ''
  try {
    if (current.kind === 'create') await folders.create(draftName.value, draftParent.value)
    if (current.kind === 'rename') await folders.rename(current.folder.id, draftName.value)
    if (current.kind === 'move') await folders.move(current.folder.id, draftParent.value)
    if (current.kind === 'file') await fileDocument(current.document.id, draftParent.value)
    if (current.kind === 'remove') {
      await folders.remove(current.folder.id, true)
      await documents.load()
      if (selected.value === current.folder.id) selected.value = null
    }
    prompt.value = null
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : t.errors.generic
  } finally {
    busy.value = false
  }
}

function confidenceTone(document: DocumentSummary): string {
  switch (document.report?.ingestion_confidence) {
    case 'high':
      return 'pass'
    case 'medium':
      return 'warn'
    case 'low':
      return 'fail'
    default:
      return 'idle'
  }
}

function confidenceLabel(document: DocumentSummary): string {
  const level = document.report?.ingestion_confidence
  return level ? t.report[level] : t.common.none
}

onMounted(() => Promise.all([documents.load(), folders.load()]))
onUnmounted(() => documents.stopPolling())
</script>

<template>
  <div class="page">
    <header class="page__head">
      <div>
        <p class="eyebrow">{{ t.app.name }}</p>
        <h1 class="h-page">{{ t.documents.title }}</h1>
        <p class="muted lead">{{ t.documents.lead }}</p>
      </div>
      <div class="row">
        <input
          ref="fileInput"
          class="sr-only"
          type="file"
          accept="application/pdf,.pdf"
          @change="pick"
        />
        <button class="btn btn--primary" :disabled="uploading" @click="fileInput?.click()">
          {{ uploading ? t.documents.uploading : t.documents.upload }}
        </button>
      </div>
    </header>

    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <div class="layout">
      <FolderTree
        :folders="folders.items"
        :selected="selected"
        :dropping="dragging"
        :total-count="documents.items.length"
        :root-count="rootCount"
        @select="selected = $event"
        @drop-document="fileDocument($event.documentId, $event.folderId)"
        @create="openPrompt({ kind: 'create', parentId: $event })"
        @rename="openPrompt({ kind: 'rename', folder: $event })"
        @move="openPrompt({ kind: 'move', folder: $event })"
        @remove="openPrompt({ kind: 'remove', folder: $event })"
      />

      <div class="pane">
        <p v-if="breadcrumb.length" class="crumbs meta">
          <span v-for="(step, index) in breadcrumb" :key="step.id">
            <button class="crumb" @click="selected = step.id">{{ step.name }}</button>
            <span v-if="index < breadcrumb.length - 1" aria-hidden="true"> / </span>
          </span>
        </p>

        <div v-if="!visible.length && !documents.loading" class="empty card">
          <p class="empty__text">
            {{ documents.items.length ? t.folders.empty : t.documents.empty }}
          </p>
          <button class="btn btn--mark" @click="fileInput?.click()">
            {{ t.documents.emptyAction }}
          </button>
        </div>

        <ul v-else class="grid">
          <li
            v-for="document in visible"
            :key="document.id"
            class="doc card"
            draggable="true"
            @dragstart="startDrag($event, document)"
            @dragend="dragging = false"
          >
            <div class="doc__head">
              <div class="grow">
                <RouterLink
                  :to="{ name: 'document', params: { id: document.id } }"
                  class="doc__title truncate"
                >
                  {{ document.title || document.filename }}
                </RouterLink>
                <p class="meta doc__file truncate">{{ document.filename }}</p>
              </div>
              <StatusPill :status="document.parse_status" kind="parse" />
            </div>

            <dl class="doc__facts">
              <div>
                <dt class="meta">{{ t.documents.pages }}</dt>
                <dd class="num">{{ document.page_count ?? t.common.none }}</dd>
              </div>
              <div>
                <dt class="meta">{{ t.documents.language }}</dt>
                <dd class="num">{{ document.language ?? t.common.none }}</dd>
              </div>
              <div>
                <dt class="meta">{{ t.report.narratable }}</dt>
                <dd class="num">{{ document.report?.narratable_words ?? t.common.none }}</dd>
              </div>
              <div>
                <dt class="meta">{{ t.report.confidence }}</dt>
                <dd>
                  <span class="badge" :class="`badge--${confidenceTone(document)}`">
                    {{ confidenceLabel(document) }}
                  </span>
                </dd>
              </div>
            </dl>

            <ConfidenceStrip v-if="document.report" :report="document.report" compact />

            <p v-if="document.parse_error" class="doc__error">
              {{ document.parse_error.split('\n')[0] }}
            </p>

            <footer class="doc__foot">
              <RouterLink
                class="btn btn--sm"
                :to="{ name: 'document', params: { id: document.id } }"
              >
                {{ t.documents.openDetail }}
              </RouterLink>
              <RouterLink
                v-if="document.parse_status === 'parsed'"
                class="btn btn--sm btn--mark"
                :to="{ name: 'new-run', params: { id: document.id } }"
              >
                {{ t.documents.newRun }}
              </RouterLink>
              <button
                class="btn btn--sm btn--ghost"
                @click="openPrompt({ kind: 'file', document })"
              >
                {{ t.folders.inFolder }}
              </button>
              <button class="btn btn--sm btn--ghost" @click="documents.reparse(document.id)">
                {{ t.documents.reparse }}
              </button>
            </footer>
          </li>
        </ul>
      </div>
    </div>

    <ModalDialog
      :open="Boolean(prompt)"
      :title="promptTitle"
      :lead="
        prompt?.kind === 'remove'
          ? t.folders.deleteLead
          : prompt?.kind === 'file'
            ? t.folders.moveDocumentLead
            : ''
      "
      @close="prompt = null"
    >
      <div class="stack">
        <p v-if="prompt?.kind === 'remove'" class="quote">{{ prompt.folder.name }}</p>

        <div v-if="prompt?.kind === 'create' || prompt?.kind === 'rename'" class="field">
          <label for="folder-name">{{ t.folders.name }}</label>
          <input id="folder-name" v-model="draftName" class="input" data-autofocus />
        </div>

        <div
          v-if="prompt?.kind === 'create' || prompt?.kind === 'move' || prompt?.kind === 'file'"
          class="field"
        >
          <label for="folder-parent">
            {{ prompt.kind === 'file' ? t.folders.title : t.folders.parent }}
          </label>
          <select id="folder-parent" v-model="draftParent" class="select">
            <option :value="null">{{ t.folders.root }}</option>
            <option v-for="folder in destinations" :key="folder.id" :value="folder.id">
              {{ folder.path.join(' / ') }}
            </option>
          </select>
        </div>
      </div>

      <template #actions>
        <button class="btn" @click="prompt = null">{{ t.common.cancel }}</button>
        <button
          class="btn"
          :class="prompt?.kind === 'remove' ? 'btn--danger' : 'btn--mark'"
          :disabled="
            busy ||
            ((prompt?.kind === 'create' || prompt?.kind === 'rename') && !draftName.trim())
          "
          @click="confirmPrompt"
        >
          {{ prompt?.kind === 'remove' ? t.common.delete : t.common.save }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.page {
  padding: var(--s6) var(--s6) var(--s8);
  max-width: 1280px;
  margin: 0 auto;
}

.page__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--s5);
  margin-bottom: var(--s6);
  flex-wrap: wrap;
}

.lead {
  margin-top: 6px;
  font-size: var(--t-sm);
}

.layout {
  display: grid;
  grid-template-columns: var(--sidebar) minmax(0, 1fr);
  gap: var(--s4);
  align-items: start;
}

.pane {
  display: grid;
  gap: var(--s3);
  min-width: 0;
}

.crumbs {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
}

.crumb {
  border: 0;
  background: transparent;
  padding: 0;
  color: var(--ink-2);
  font: inherit;
  cursor: pointer;
}

.crumb:hover {
  color: var(--mark);
}

.grid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: var(--s4);
}

.doc[draggable='true'] {
  cursor: grab;
}

.doc:active {
  cursor: grabbing;
}

.quote {
  padding: var(--s3);
  border-radius: var(--r-md);
  background: var(--chrome);
  font-size: var(--t-sm);
}

@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
}

.doc {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
  padding: var(--s4);
  transition:
    border-color var(--fast),
    box-shadow var(--fast),
    transform var(--fast);
}

.doc:hover {
  border-color: var(--rule-strong);
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

.doc__head {
  display: flex;
  align-items: flex-start;
  gap: var(--s3);
}

.doc__title {
  display: block;
  font-size: var(--t-md);
  font-weight: 600;
  color: var(--ink);
  text-decoration: none;
  letter-spacing: -0.01em;
}

.doc__title:hover {
  color: var(--mark);
}

.doc__file {
  margin-top: 2px;
}

.doc__facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--s3);
  margin: 0;
}

.doc__facts dt {
  font-size: 0.625rem;
}

.doc__facts dd {
  margin: 2px 0 0;
  font-size: var(--t-sm);
}

.doc__error {
  font-size: var(--t-xs);
  color: var(--fail);
  background: var(--fail-soft);
  padding: var(--s2) var(--s3);
  border-radius: var(--r-sm);
}

.doc__foot {
  display: flex;
  gap: var(--s2);
  margin-top: auto;
  padding-top: var(--s2);
  flex-wrap: wrap;
}

.doc__foot .btn {
  text-decoration: none;
}

.empty {
  display: grid;
  place-items: center;
  gap: var(--s4);
  padding: var(--s8) var(--s5);
  text-align: center;
}

.empty__text {
  color: var(--ink-3);
  max-width: 42ch;
}

.error {
  margin-bottom: var(--s4);
  color: var(--fail);
  font-size: var(--t-sm);
}
</style>
