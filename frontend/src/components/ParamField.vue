<script setup lang="ts">
/**
 * One editable node parameter.
 *
 * The node declares the type, so the control follows from the data rather than
 * from a switch maintained here per node. A parameter left untouched shows the
 * node's own default as placeholder text and is not written into the saved
 * definition at all — that is what keeps the artifact cache valid across a save
 * that changed nothing.
 */
import { computed } from 'vue'

import type { NodeParam } from '@/api/types'
import { t } from '@/i18n'

const props = defineProps<{
  param: NodeParam
  value: unknown
  models: string[]
}>()
const emit = defineEmits<{ (e: 'update', value: unknown): void }>()

const isSet = computed(() => props.value !== undefined && props.value !== null && props.value !== '')

const changed = computed(
  () => isSet.value && String(props.value) !== String(props.param.default ?? ''),
)

const defaultText = computed(() =>
  props.param.default === null || props.param.default === undefined
    ? ''
    : String(props.param.default),
)

const text = computed(() => (isSet.value ? String(props.value) : ''))

const checked = computed(() => (isSet.value ? Boolean(props.value) : Boolean(props.param.default)))

function onText(event: Event): void {
  emit('update', (event.target as HTMLInputElement | HTMLTextAreaElement).value)
}

function onNumber(event: Event): void {
  const raw = (event.target as HTMLInputElement).value
  emit('update', raw === '' ? null : Number(raw))
}

function onBool(event: Event): void {
  emit('update', (event.target as HTMLInputElement).checked)
}

function reset(): void {
  emit('update', null)
}
</script>

<template>
  <div class="param" :class="{ 'param--prompt': param.type === 'prompt' }">
    <div class="param__head">
      <label class="param__label" :for="`p-${param.key}`">{{ param.label }}</label>
      <code class="param__key">{{ param.key }}</code>
      <span v-if="changed" class="badge badge--mark">{{ t.pipelines.promptChanged }}</span>
      <span class="grow" />
      <button v-if="isSet" class="btn btn--ghost btn--sm" @click="reset">
        {{ param.type === 'prompt' ? t.pipelines.promptDefault : t.common.reset }}
      </button>
    </div>

    <p v-if="param.description" class="param__hint">{{ param.description }}</p>

    <textarea
      v-if="param.type === 'prompt'"
      :id="`p-${param.key}`"
      class="textarea prompt"
      spellcheck="false"
      rows="16"
      :value="text || defaultText"
      @input="onText"
    />

    <textarea
      v-else-if="param.type === 'text'"
      :id="`p-${param.key}`"
      class="textarea"
      rows="4"
      :value="text"
      :placeholder="defaultText"
      @input="onText"
    />

    <label v-else-if="param.type === 'bool'" class="switch">
      <input :id="`p-${param.key}`" type="checkbox" :checked="checked" @change="onBool" />
      <span>{{ checked ? t.common.yes : t.common.no }}</span>
    </label>

    <select
      v-else-if="param.type === 'select'"
      :id="`p-${param.key}`"
      class="select"
      :value="text"
      @change="onText"
    >
      <option value="">{{ t.common.default }}{{ defaultText ? ` · ${defaultText}` : '' }}</option>
      <option v-for="option in param.options" :key="option" :value="option">{{ option }}</option>
    </select>

    <select
      v-else-if="param.type === 'model'"
      :id="`p-${param.key}`"
      class="select"
      :value="text"
      @change="onText"
    >
      <option value="">{{ t.common.default }}{{ defaultText ? ` · ${defaultText}` : '' }}</option>
      <option v-for="model in models" :key="model" :value="model">{{ model }}</option>
    </select>

    <input
      v-else-if="param.type === 'int' || param.type === 'float'"
      :id="`p-${param.key}`"
      class="input input--num"
      type="number"
      :step="param.type === 'int' ? 1 : 0.05"
      :min="param.minimum ?? undefined"
      :max="param.maximum ?? undefined"
      :value="text"
      :placeholder="defaultText"
      @input="onNumber"
    />

    <input
      v-else
      :id="`p-${param.key}`"
      class="input"
      type="text"
      :value="text"
      :placeholder="defaultText"
      @input="onText"
    />
  </div>
</template>

<style scoped>
.param {
  display: grid;
  gap: 6px;
  padding: var(--s3);
  border-radius: var(--r-md);
  background: var(--chrome);
  border: 1px solid var(--rule);
}

.param--prompt {
  grid-column: 1 / -1;
}

.param__head {
  display: flex;
  align-items: center;
  gap: var(--s2);
}

.param__label {
  font-size: var(--t-sm);
  font-weight: 600;
  color: var(--ink);
}

.param__key {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-4);
}

.param__hint {
  margin: 0;
  font-size: var(--t-xs);
  color: var(--ink-3);
  line-height: 1.5;
  max-width: 78ch;
}

.prompt {
  font-family: var(--mono);
  font-size: var(--t-xs);
  line-height: 1.6;
  min-height: 260px;
}

.input--num {
  max-width: 180px;
}

.switch {
  display: flex;
  align-items: center;
  gap: var(--s2);
  font-size: var(--t-sm);
  color: var(--ink-2);
}
</style>
