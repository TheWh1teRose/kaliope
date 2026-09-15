<script setup lang="ts">
/**
 * The revision history of a pipeline or a format.
 *
 * Append-only on the server, so this list only ever grows: a restore appends the
 * old content as a new revision instead of rewinding. That is why the newest
 * entry is always the one in force, and why an entry can say which revision it
 * was restored from.
 */
import type { RevisionOut } from '@/api/types'
import { t } from '@/i18n'

defineProps<{ revisions: RevisionOut[]; current: number; busy?: boolean }>()
const emit = defineEmits<{ (e: 'restore', revision: number): void }>()

function when(value: string): string {
  if (!value) return t.common.none
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' })
}
</script>

<template>
  <ul class="revisions">
    <li
      v-for="revision in revisions"
      :key="revision.revision"
      class="revision"
      :class="{ 'revision--current': revision.revision === current }"
    >
      <span class="revision__no num">r{{ revision.revision }}</span>
      <span class="revision__body">
        <span class="revision__note">{{ revision.note || t.common.none }}</span>
        <span class="revision__meta meta">
          {{ when(revision.created_at) }}
          <template v-if="revision.created_by_email">
            · {{ t.pipelines.by }} {{ revision.created_by_email }}
          </template>
          <template v-if="revision.restored_from">
            · {{ t.pipelines.restoredFrom }} {{ revision.restored_from }}
          </template>
        </span>
      </span>
      <span class="revision__version num">{{ revision.version }}</span>
      <button
        v-if="revision.revision !== current"
        class="btn btn--ghost btn--sm"
        :disabled="busy"
        @click="emit('restore', revision.revision)"
      >
        {{ t.pipelines.restore }}
      </button>
    </li>
  </ul>
</template>

<style scoped>
.revisions {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s1);
}

.revision {
  display: flex;
  align-items: center;
  gap: var(--s3);
  padding: var(--s2) var(--s3);
  border-radius: var(--r-sm);
  background: var(--chrome);
  border-left: 3px solid transparent;
}

.revision--current {
  border-left-color: var(--mark);
  background: var(--mark-soft);
}

.revision__no {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
  width: 34px;
}

.revision__body {
  display: grid;
  gap: 1px;
  flex: 1;
  min-width: 0;
}

.revision__note {
  font-size: var(--t-sm);
  color: var(--ink);
}

.revision__meta {
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.revision__version {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
}
</style>
