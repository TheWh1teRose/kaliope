<script setup lang="ts">
/**
 * Node parameters, with thinking effort beside every model choice.
 *
 * The effort list is what the selected model accepts (`GET /api/models`, the
 * same catalogue the experiment settings use). Empty means the model's own
 * default and is not stored. Switching to a model that does not offer the
 * current effort clears it.
 */
import { computed } from 'vue'

import type { Effort, ModelCatalogue, ModelInfo, NodeParam } from '@/api/types'
import ParamField from '@/components/ParamField.vue'
import { t } from '@/i18n'

const props = defineProps<{
  params: NodeParam[]
  config: Record<string, unknown>
  models: string[]
  catalogue: ModelCatalogue | null
}>()

const emit = defineEmits<{ (e: 'update', key: string, value: unknown): void }>()

const labels = t.modelSettings

const shown = computed(() => props.params.filter((param) => param.key !== 'effort'))

function modelOf(value: unknown): ModelInfo | undefined {
  const chosen = typeof value === 'string' && value ? value : (props.catalogue?.default_model ?? '')
  if (!chosen || !props.catalogue) return undefined
  return props.catalogue.models.find((model) => model.id === chosen)
}

function levels(value: unknown): Effort[] {
  const model = modelOf(value)
  if (model) return model.effort_levels
  const declared = props.params.find((param) => param.key === 'effort')
  return (declared?.options ?? []) as Effort[]
}

function onModel(key: string, value: unknown): void {
  emit('update', key, value)
  const effort = props.config.effort
  if (typeof effort !== 'string' || !effort) return
  const model = modelOf(value)
  if (model && !model.effort_levels.includes(effort as Effort)) emit('update', 'effort', null)
}

function onEffort(event: Event): void {
  const raw = (event.target as HTMLSelectElement).value
  emit('update', 'effort', raw === '' ? null : raw)
}

function effortLabel(level: Effort): string {
  return labels.effortLevels[level]
}

function defaultLabel(value: unknown): string {
  const model = modelOf(value)
  if (model && !model.effort_levels.length) return labels.effortUnsupported
  if (model?.default_effort) return `${labels.effortDefault} (${effortLabel(model.default_effort)})`
  return labels.effortDefault
}

function effortValue(): string {
  return typeof props.config.effort === 'string' ? props.config.effort : ''
}
</script>

<template>
  <template v-for="param in shown" :key="param.key">
    <div v-if="param.type === 'model'" class="pair">
      <ParamField
        :param="param"
        :value="config[param.key]"
        :models="models"
        @update="(value) => onModel(param.key, value)"
      />
      <div class="effort" data-param="effort">
        <div class="effort__head">
          <label class="effort__label" :for="`effort-${param.key}`">{{ labels.effort }}</label>
          <code class="effort__key">effort</code>
          <span class="grow" />
          <button
            v-if="effortValue()"
            class="btn btn--ghost btn--sm"
            type="button"
            @click="emit('update', 'effort', null)"
          >
            {{ t.common.reset }}
          </button>
        </div>
        <p class="effort__hint">
          {{ levels(config[param.key]).length ? labels.effortHint : labels.effortNone }}
        </p>
        <select
          :id="`effort-${param.key}`"
          class="select"
          data-effort
          :disabled="Boolean(modelOf(config[param.key])) && !levels(config[param.key]).length"
          :value="effortValue()"
          @change="onEffort"
        >
          <option value="">{{ defaultLabel(config[param.key]) }}</option>
          <option v-for="level in levels(config[param.key])" :key="level" :value="level">
            {{ effortLabel(level) }}
          </option>
        </select>
      </div>
    </div>
    <ParamField
      v-else
      :param="param"
      :value="config[param.key]"
      :models="models"
      @update="(value) => emit('update', param.key, value)"
    />
  </template>
</template>

<style scoped>
.pair {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr));
  gap: var(--s2);
  align-items: start;
}

.effort {
  display: grid;
  gap: 6px;
  padding: var(--s3);
  border-radius: var(--r-md);
  background: var(--chrome);
  border: 1px solid var(--rule);
  min-width: 0;
}

.effort__head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.effort__label {
  font-size: var(--t-sm);
  font-weight: 600;
  color: var(--ink);
}

.effort__key {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-4);
}

.effort__hint {
  margin: 0;
  font-size: var(--t-xs);
  color: var(--ink-3);
  line-height: 1.5;
}
</style>
