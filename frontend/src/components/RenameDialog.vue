<script setup lang="ts">
/**
 * Renaming a run or a series: one display name that may be left blank, so the
 * default title applies again. The caller passes how to save; the dialog keeps
 * the draft, the busy state and any refusal, and closes once it is saved.
 */
import { ref, useId, watch } from 'vue'

import { ApiError } from '@/api/client'
import ModalDialog from '@/components/ModalDialog.vue'
import { t } from '@/i18n'

const props = defineProps<{
  open: boolean
  title: string
  lead?: string
  /** Under the field: what a blank name falls back to. */
  hint: string
  /** The name set now; `null` when the default title applies. */
  current: string | null | undefined
  save: (name: string) => Promise<unknown>
}>()

const emit = defineEmits<{ (event: 'close'): void; (event: 'saved'): void }>()

const fieldId = useId()
const draft = ref('')
const busy = ref(false)
const error = ref('')

watch(
  () => props.open,
  (open) => {
    if (!open) return
    draft.value = props.current ?? ''
    error.value = ''
  },
  { immediate: true },
)

async function confirm(): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await props.save(draft.value.trim())
    emit('saved')
    emit('close')
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <ModalDialog :open="open" :title="title" :lead="lead" @close="emit('close')">
    <div class="field">
      <label :for="fieldId">{{ t.run.name }}</label>
      <input
        :id="fieldId"
        v-model="draft"
        class="input"
        maxlength="200"
        :placeholder="t.run.namePlaceholder"
        data-autofocus
        data-rename-input
        @keydown.enter.prevent="confirm"
      />
      <p class="meta hint">{{ hint }}</p>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <template #actions>
      <button class="btn" type="button" @click="emit('close')">{{ t.common.cancel }}</button>
      <button
        class="btn btn--mark"
        type="button"
        data-action="confirm-rename"
        :disabled="busy"
        @click="confirm"
      >
        {{ busy ? t.common.saving : t.common.save }}
      </button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.hint {
  margin-top: 4px;
}

.error {
  margin-top: var(--s3);
  color: var(--fail);
  font-size: var(--t-sm);
}
</style>
