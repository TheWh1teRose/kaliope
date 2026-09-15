<script setup lang="ts">
/**
 * Reason codes as a keyboard-first list.
 *
 * AC-UI-3 requires that saving an edit without a reason code is impossible, so
 * the required step is made the fastest one: press 1–9 to pick, and the save
 * button stays disabled until a code — and, where the code demands it, a note —
 * is present.
 */
import { computed, onMounted, onUnmounted } from 'vue'

import { useCatalogueStore } from '@/stores/catalogue'
import { t } from '@/i18n'

const props = defineProps<{ modelValue: string | null; note: string; autofocus?: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'update:note', value: string): void
}>()

const catalogue = useCatalogueStore()

const needsNote = computed(() => {
  const spec = props.modelValue ? catalogue.reasonSpec(props.modelValue) : undefined
  return spec?.requires_note ?? false
})

const noteMissing = computed(() => needsNote.value && !props.note.trim())

defineExpose({ needsNote, noteMissing })

function onKey(event: KeyboardEvent): void {
  if (event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLInputElement) return
  const index = Number.parseInt(event.key, 10)
  if (Number.isNaN(index) || index < 1 || index > catalogue.reasonCodes.length) return
  event.preventDefault()
  emit('update:modelValue', catalogue.reasonCodes[index - 1].code)
}

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="stack">
    <fieldset class="codes">
      <legend class="eyebrow">{{ t.review.reason }}</legend>
      <button
        v-for="(spec, index) in catalogue.reasonCodes"
        :key="spec.code"
        type="button"
        class="code"
        :class="{ 'code--on': modelValue === spec.code }"
        :data-autofocus="autofocus && index === 0 ? '' : undefined"
        @click="emit('update:modelValue', spec.code)"
      >
        <kbd class="code__key">{{ index + 1 }}</kbd>
        <span class="code__label">{{ spec.label_de }}</span>
        <span class="code__id meta">{{ spec.code }}</span>
      </button>
    </fieldset>

    <div class="field">
      <label for="reason-note">
        {{ t.review.note }}<span v-if="needsNote" aria-hidden="true"> *</span>
      </label>
      <textarea
        id="reason-note"
        class="textarea"
        rows="3"
        :value="note"
        :aria-invalid="noteMissing"
        @input="emit('update:note', ($event.target as HTMLTextAreaElement).value)"
      />
      <p v-if="noteMissing" class="hint hint--bad">{{ t.review.noteRequired }}</p>
    </div>
  </div>
</template>

<style scoped>
.codes {
  border: 0;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 3px;
}

.codes legend {
  margin-bottom: var(--s2);
}

.code {
  display: grid;
  grid-template-columns: 22px 1fr auto;
  align-items: center;
  gap: var(--s3);
  width: 100%;
  padding: 7px 9px;
  border: 1px solid transparent;
  border-radius: var(--r-md);
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition:
    background var(--fast),
    border-color var(--fast);
}

.code:hover {
  background: var(--chrome);
}

.code--on {
  background: var(--mark-soft);
  border-color: var(--mark);
}

.code__key {
  display: grid;
  place-items: center;
  height: 20px;
  border-radius: 4px;
  border: 1px solid var(--rule-strong);
  background: var(--card);
  font-family: var(--mono);
  font-size: 0.625rem;
  color: var(--ink-3);
}

.code--on .code__key {
  border-color: var(--mark);
  color: var(--mark-deep);
}

.code__label {
  font-size: var(--t-sm);
}

.code__id {
  font-size: 0.625rem;
}

.hint {
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.hint--bad {
  color: var(--fail);
}
</style>
