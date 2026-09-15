<script setup lang="ts">
/**
 * The folder sidebar.
 *
 * Drop targets as well as filters: dragging a document card onto a folder is
 * the fastest way to file one, and the same rows serve both jobs. The server
 * returns the tree already ordered with each row's depth, so indentation here
 * is presentation only — no hierarchy is rebuilt in the client.
 */
import { ref } from 'vue'

import type { FolderOut } from '@/api/types'
import { t } from '@/i18n'

const props = defineProps<{
  folders: FolderOut[]
  /** `null` shows every document, `'root'` the unfiled ones, else a folder id. */
  selected: string | null
  /** `true` while a document is being dragged, so drop targets can light up. */
  dropping?: boolean
  totalCount: number
  rootCount: number
}>()

const emit = defineEmits<{
  (event: 'select', id: string | null): void
  (event: 'drop-document', payload: { documentId: string; folderId: string | null }): void
  (event: 'create', parentId: string | null): void
  (event: 'rename', folder: FolderOut): void
  (event: 'move', folder: FolderOut): void
  (event: 'remove', folder: FolderOut): void
}>()

/** `undefined` is "no row hovered"; `null` is the root row. */
const over = ref<string | null | undefined>(undefined)

function onDrop(event: DragEvent, folderId: string | null): void {
  over.value = undefined
  const documentId = event.dataTransfer?.getData('text/kalliope-document')
  if (documentId) emit('drop-document', { documentId, folderId })
}

function allowDrop(event: DragEvent, folderId: string | null): void {
  if (!props.dropping) return
  event.preventDefault()
  over.value = folderId
}
</script>

<template>
  <nav class="tree">
    <div class="tree__head">
      <p class="eyebrow">{{ t.folders.title }}</p>
      <button class="btn btn--sm btn--ghost" @click="emit('create', null)">
        + {{ t.folders.new }}
      </button>
    </div>

    <ul class="rows">
      <li>
        <button
          class="row"
          :class="{ 'row--on': selected === null }"
          @click="emit('select', null)"
        >
          <span class="row__icon" aria-hidden="true">◫</span>
          <span class="row__name grow">{{ t.folders.all }}</span>
          <span class="row__count num">{{ totalCount }}</span>
        </button>
      </li>

      <li>
        <button
          class="row"
          :class="{ 'row--on': selected === 'root', 'row--over': over === null }"
          @click="emit('select', 'root')"
          @dragover="allowDrop($event, null)"
          @dragleave="over = undefined"
          @drop.prevent="onDrop($event, null)"
        >
          <span class="row__icon" aria-hidden="true">↥</span>
          <span class="row__name grow">{{ t.folders.root }}</span>
          <span class="row__count num">{{ rootCount }}</span>
        </button>
      </li>

      <li v-for="folder in folders" :key="folder.id">
        <div class="rowline">
          <button
            class="row"
            :class="{ 'row--on': selected === folder.id, 'row--over': over === folder.id }"
            :style="{ paddingLeft: `${8 + folder.depth * 14}px` }"
            @click="emit('select', folder.id)"
            @dragover="allowDrop($event, folder.id)"
            @dragleave="over = undefined"
            @drop.prevent="onDrop($event, folder.id)"
          >
            <span class="row__icon" aria-hidden="true">▸</span>
            <span class="row__name grow truncate">{{ folder.name }}</span>
            <span class="row__count num">{{ folder.total_document_count }}</span>
          </button>

          <div class="rowline__acts">
            <button
              class="icon"
              :title="t.folders.new"
              @click.stop="emit('create', folder.id)"
            >
              +
            </button>
            <button class="icon" :title="t.common.rename" @click.stop="emit('rename', folder)">
              ✎
            </button>
            <button class="icon" :title="t.common.move" @click.stop="emit('move', folder)">
              ⇄
            </button>
            <button
              class="icon icon--danger"
              :title="t.common.delete"
              @click.stop="emit('remove', folder)"
            >
              ×
            </button>
          </div>
        </div>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.tree {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  padding: var(--s3);
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  align-self: start;
  position: sticky;
  top: var(--s4);
}

.tree__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s2);
}

.rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 1px;
  max-height: 66vh;
  overflow-y: auto;
}

.rowline {
  display: flex;
  align-items: center;
}

.rowline__acts {
  display: none;
  gap: 1px;
  padding-right: 2px;
}

.rowline:hover .rowline__acts {
  display: flex;
}

.row {
  display: flex;
  align-items: center;
  gap: var(--s2);
  width: 100%;
  min-width: 0;
  padding: 5px var(--s2);
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--ink-2);
  font-size: var(--t-sm);
  text-align: left;
  cursor: pointer;
  transition:
    background var(--fast),
    color var(--fast);
}

.row:hover {
  background: var(--chrome);
  color: var(--ink);
}

.row--on {
  background: var(--ink);
  color: var(--chrome);
}

.row--on .row__count,
.row--on .row__icon {
  color: inherit;
  opacity: 0.75;
}

.row--over {
  outline: 1px dashed var(--mark);
  background: var(--mark-soft);
  color: var(--mark-deep);
}

.row__icon {
  color: var(--ink-4);
  font-size: 0.75rem;
  width: 12px;
}

.row__count {
  font-size: 0.625rem;
  color: var(--ink-3);
}

.icon {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--ink-3);
  font-size: var(--t-xs);
  cursor: pointer;
}

.icon:hover {
  background: var(--sunk);
  color: var(--ink);
}

.icon--danger:hover {
  background: var(--fail-soft);
  color: var(--fail);
}
</style>
