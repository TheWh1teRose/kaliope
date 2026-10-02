<script setup lang="ts">
/**
 * One named input field of an experiment: key, a short peek, where the value
 * came from, and an editable text body. Removable when it is a custom field.
 */
import { computed, ref } from 'vue'

import { t } from '@/i18n'

export type FieldOrigin = 'run' | 'sample' | 'edited' | 'custom'

const props = defineProps<{
  name: string
  modelValue: string
  origin: FieldOrigin
  label?: string
  hint?: string | null
  multiline?: boolean
  removable?: boolean
  startOpen?: boolean
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'remove'): void
}>()

const open = ref(Boolean(props.startOpen))
const peek = computed(() => props.modelValue.replace(/\s+/g, ' ').trim())
const badge = computed(() => {
  if (!props.modelValue.trim()) return { cls: 'badge--fail', text: t.experiments.empty }
  switch (props.origin) {
    case 'run':
      return { cls: 'badge--idle', text: t.experiments.fromRun }
    case 'sample':
      return { cls: 'badge--idle', text: t.experiments.sample }
    case 'edited':
      return { cls: 'badge--warn', text: t.experiments.edited }
    default:
      return { cls: 'badge--mark', text: t.experiments.custom }
  }
})
</script>

<template>
  <div class="vcard" :class="{ 'vcard--open': open }">
    <button class="vcard__head" :aria-expanded="open" @click="open = !open">
      <code class="vcard__key">{{ name }}</code>
      <span class="vcard__peek truncate grow">{{ peek || label }}</span>
      <span class="badge" :class="badge.cls">{{ badge.text }}</span>
      <span class="chev" aria-hidden="true">{{ open ? '−' : '+' }}</span>
    </button>
    <div v-if="open" class="vcard__body">
      <p v-if="label || hint" class="meta">{{ label }}<template v-if="hint"> · {{ hint }}</template></p>
      <textarea
        class="textarea"
        :rows="multiline || modelValue.length > 80 ? 6 : 2"
        :value="modelValue"
        :aria-label="name"
        @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
      />
      <div v-if="removable" class="row">
        <span class="grow" />
        <button class="btn btn--ghost btn--sm btn--danger" @click="emit('remove')">
          {{ t.experiments.removeField }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.vcard {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-sm);
}

.vcard__head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  width: 100%;
  min-width: 0;
  padding: 10px var(--s3);
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.vcard__key {
  font-family: var(--mono);
  font-size: var(--t-sm);
}

.vcard__peek {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.chev {
  color: var(--ink-3);
}

.vcard__body {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  padding: 0 var(--s3) var(--s3);
}
</style>
