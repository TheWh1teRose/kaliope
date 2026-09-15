<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import type { FeedbackOut, Note, RunOut } from '@/api/types'
import NotesEditor from '@/components/NotesEditor.vue'
import { t } from '@/i18n'
import { useRunsStore } from '@/stores/runs'

const props = defineProps<{ id: string }>()

const runs = useRunsStore()
const router = useRouter()

const run = ref<RunOut | null>(null)
const feedback = ref<FeedbackOut | null>(null)
const notes = ref<Note[]>([])
const busy = ref(false)
const error = ref('')

async function load(): Promise<void> {
  error.value = ''
  run.value = await runs.get(props.id)
  feedback.value = await runs.feedback(props.id)
  notes.value = [...feedback.value.draft_notes]
}

async function save(): Promise<void> {
  if (!feedback.value) return
  busy.value = true
  error.value = ''
  try {
    feedback.value = await runs.saveFeedback(props.id, notes.value)
    notes.value = [...feedback.value.draft_notes]
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

async function submit(): Promise<void> {
  if (!feedback.value) return
  busy.value = true
  error.value = ''
  try {
    await runs.submitFeedback(props.id, notes.value)
    await router.push({ name: 'run', params: { id: props.id } })
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <p v-if="error" class="notice notice--fail">{{ error }}</p>
    <p v-if="!feedback" class="muted pad">{{ t.common.loading }}</p>
    <NotesEditor
      v-else
      :feedback="feedback"
      :notes="notes"
      :busy="busy"
      @update:notes="notes = $event"
      @save="save"
      @submit="submit"
    />
    <RouterLink v-if="run" :to="{ name: 'run', params: { id: props.id } }" class="back">
      ← {{ t.run.title }}
    </RouterLink>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  position: relative;
}

.back {
  position: absolute;
  top: var(--s3);
  left: var(--s4);
  z-index: 2;
  text-decoration: none;
  color: var(--ink-3);
  font-size: var(--t-xs);
}

.pad {
  padding: var(--s5);
}
</style>
