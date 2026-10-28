<script setup lang="ts">
import { ref } from 'vue'

import { ApiError, api } from '@/api/client'
import { t } from '@/i18n'

/**
 * Downloads everything the pipeline graph shows as one zip. The server builds
 * the file on request, which takes a moment for a long series, so the button
 * fetches it itself and shows that it is working instead of a bare link.
 */
const props = defineProps<{
  /** The bundle endpoint, such as `/api/runs/<id>/bundle`. */
  path: string
  hint: string
  trashed?: boolean
}>()

const building = ref(false)
const error = ref('')

async function download(): Promise<void> {
  if (building.value || props.trashed) return
  building.value = true
  error.value = ''
  try {
    const { blob, filename } = await api.download(props.path)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    building.value = false
  }
}
</script>

<template>
  <span class="bundle">
    <button
      class="btn btn--sm"
      type="button"
      data-bundle
      :disabled="building || trashed"
      :aria-busy="building"
      :title="trashed ? t.graph.bundleTrashed : hint"
      @click="download"
    >
      <span v-if="building" class="spinner" aria-hidden="true" />
      {{ building ? t.graph.bundleBuilding : t.graph.bundle }}
    </button>
    <span v-if="error" class="notice notice--fail bundle__error" role="alert">{{ error }}</span>
  </span>
</template>

<style scoped>
.bundle {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
}

.bundle__error {
  margin: 0;
}

.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--rule-strong);
  border-top-color: var(--mark);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
