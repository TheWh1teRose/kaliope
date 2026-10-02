<script setup lang="ts">
/**
 * "In Ordner …": pick the Sammlung folder one or more outputs go into. The
 * keyboard and touch way to file, next to dragging a card onto the tree.
 */
import { computed, ref, watch } from 'vue'

import ModalDialog from '@/components/ModalDialog.vue'
import { t } from '@/i18n'
import { useOutputFoldersStore } from '@/stores/folders'

const props = defineProps<{
  open: boolean
  /** How many outputs move. */
  count: number
  /** Where a single output sits now; `undefined` when several move. */
  current?: string | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'move', folderId: string | null): void
}>()

const folders = useOutputFoldersStore()
const labels = t.collection
/** `''` is "Ohne Ordner"; `undefined` is nothing picked yet. */
const choice = ref<string | undefined>(undefined)
const query = ref('')

watch(
  () => props.open,
  (open) => {
    if (!open) return
    query.value = ''
    choice.value = props.current === undefined ? undefined : (props.current ?? '')
    if (!folders.items.length) void folders.load()
  },
  { immediate: true },
)

const rows = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return folders.items.filter(
    (folder) => !needle || folder.path.join(' / ').toLowerCase().includes(needle),
  )
})

function confirm(): void {
  if (choice.value === undefined) return
  emit('move', choice.value || null)
}
</script>

<template>
  <ModalDialog
    :open="open"
    :title="labels.moveTitle"
    :lead="count > 1 ? `${count} ${labels.outputs} · ${labels.moveManyLead}` : labels.moveLead"
    @close="emit('close')"
  >
    <div class="stack">
      <input
        v-model="query"
        class="input"
        type="search"
        :placeholder="labels.findFolder"
        :aria-label="labels.findFolder"
        data-autofocus
      />
      <div class="pick" role="radiogroup" :aria-label="labels.folders">
        <label class="pick__row">
          <input v-model="choice" type="radio" name="dest" value="" />
          <span aria-hidden="true">↥</span> {{ labels.root }}
        </label>
        <label
          v-for="folder in rows"
          :key="folder.id"
          class="pick__row"
          :style="{ paddingLeft: query ? undefined : `${8 + folder.depth * 14}px` }"
        >
          <input v-model="choice" type="radio" name="dest" :value="folder.id" />
          <span aria-hidden="true">▸</span>
          {{ query ? folder.path.join(' / ') : folder.name }}
        </label>
      </div>
    </div>
    <template #actions>
      <button class="btn" @click="emit('close')">{{ t.common.cancel }}</button>
      <button class="btn btn--primary" :disabled="choice === undefined" @click="confirm">
        {{ labels.move }}
      </button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.stack {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.pick {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 320px;
  overflow: auto;
  padding: 4px;
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
}

.pick__row {
  display: flex;
  align-items: center;
  gap: var(--s2);
  padding: 6px 8px;
  border-radius: var(--r-sm);
  font-size: var(--t-sm);
  cursor: pointer;
}

.pick__row:hover {
  background: var(--chrome);
}

.pick__row:has(input:checked) {
  background: var(--mark-soft);
  color: var(--mark-deep);
}

.pick__row input {
  margin: 0;
  accent-color: var(--mark);
}
</style>
