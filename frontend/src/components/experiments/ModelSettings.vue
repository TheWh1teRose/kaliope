<script setup lang="ts">
/**
 * Model settings for an experiment, gated per model by the registry.
 *
 * Every experiment page uses this one form, so the parameters look and behave
 * the same everywhere. A control the selected model rejects is disabled with a
 * one-line reason; an empty number field means "not sent", so the model's own
 * default applies.
 */
import { computed } from 'vue'

import type {
  Effort,
  ModelCatalogue,
  ModelInfo,
  ModelSettings,
  ProviderName,
  ThinkingMode,
} from '@/api/types'
import {
  THINKING_MAX_TOKENS,
  defaultSettings,
  fitToModel,
  settingsState,
} from '@/experiments/modelSettings'
import { t } from '@/i18n'

const props = defineProps<{
  catalogue: ModelCatalogue
  modelValue: ModelSettings
}>()
const emit = defineEmits<{ (e: 'update:modelValue', value: ModelSettings): void }>()

const labels = t.modelSettings

const current = computed<ModelInfo | undefined>(() =>
  props.catalogue.models.find((m) => m.id === props.modelValue.model),
)
const state = computed(() =>
  current.value ? settingsState(current.value, props.modelValue) : null,
)

const available = computed(
  () => new Map(props.catalogue.providers.map((p) => [p.name, p.available] as const)),
)
const providerModels = computed(() =>
  props.catalogue.models.filter((m) => m.provider === current.value?.provider),
)

function usd(value: number): string {
  return `$${value.toFixed(2)}`
}

function update(patch: Partial<ModelSettings>): void {
  emit('update:modelValue', { ...props.modelValue, ...patch })
}

function selectModel(id: string): void {
  const next = props.catalogue.models.find((m) => m.id === id)
  if (next) emit('update:modelValue', fitToModel(next, props.modelValue))
}

function selectProvider(name: ProviderName): void {
  const first = props.catalogue.models.find((m) => m.provider === name)
  if (first) selectModel(first.id)
}

function numberOrNull(event: Event): number | null {
  const raw = (event.target as HTMLInputElement).value
  return raw === '' ? null : Number(raw)
}

function selectThinking(mode: ThinkingMode): void {
  const model = current.value
  const patch: Partial<ModelSettings> = { thinking: mode }
  if (mode === 'budget') {
    patch.thinking_budget = props.modelValue.thinking_budget ?? model?.min_thinking_budget ?? null
  } else {
    patch.thinking_budget = null
  }
  update(patch)
}

function selectEffort(value: string): void {
  update({ effort: value === '' ? null : (value as Effort) })
}

function reset(): void {
  if (current.value) {
    emit('update:modelValue', defaultSettings(current.value, props.modelValue.max_tokens))
  }
}

const thinkingDefaultLabel = computed(() =>
  current.value?.thinking_default === 'on' ? labels.thinkingDefaultOn : labels.thinkingDefaultOff,
)

const thinkingHint = computed(() => {
  const model = current.value
  if (!model) return ''
  if (props.modelValue.thinking === 'budget') {
    const below = model.provider === 'anthropic' ? ` · ${labels.budgetBelowMax}` : ''
    return `${labels.budgetHint} ${model.min_thinking_budget.toLocaleString('de-DE')}${below}`
  }
  return model.thinking_default === 'on' ? labels.thinksByDefault : labels.noThinkingByDefault
})

const changed = computed(() => {
  const s = props.modelValue
  return (
    s.temperature !== null ||
    s.top_p !== null ||
    s.top_k !== null ||
    s.thinking !== 'default' ||
    s.effort !== null
  )
})
</script>

<template>
  <div v-if="current && state" class="settings">
    <div class="settings__head full">
      <span class="eyebrow">{{ labels.model }}</span>
      <span class="grow" />
      <button v-if="changed" class="btn btn--ghost btn--sm" @click="reset">
        {{ labels.reset }}
      </button>
    </div>

    <div class="field">
      <label for="ms-provider">{{ labels.provider }}</label>
      <select
        id="ms-provider"
        class="select"
        :value="current.provider"
        @change="selectProvider(($event.target as HTMLSelectElement).value as ProviderName)"
      >
        <option
          v-for="provider in catalogue.providers"
          :key="provider.name"
          :value="provider.name"
          :disabled="!provider.available"
        >
          {{ provider.name }}{{ provider.available ? '' : ` · ${labels.providerUnavailable}` }}
        </option>
      </select>
    </div>

    <div class="field">
      <label for="ms-model">{{ labels.model }}</label>
      <select
        id="ms-model"
        class="select"
        :value="modelValue.model"
        @change="selectModel(($event.target as HTMLSelectElement).value)"
      >
        <option
          v-for="model in providerModels"
          :key="model.id"
          :value="model.id"
          :disabled="available.get(model.provider) === false"
        >
          {{ model.id }} · {{ usd(model.input_usd_per_mtok) }} /
          {{ usd(model.output_usd_per_mtok) }}
        </option>
      </select>
    </div>

    <div class="field full">
      <label for="ms-temperature">{{ labels.temperature }}</label>
      <div class="slider" :class="{ 'slider--off': state.samplingBlock }">
        <input
          type="range"
          min="0"
          max="2"
          step="0.1"
          :aria-label="labels.temperature"
          :disabled="!!state.samplingBlock"
          :value="modelValue.temperature ?? 1"
          @input="update({ temperature: numberOrNull($event) })"
        />
        <input
          id="ms-temperature"
          class="input num"
          type="number"
          min="0"
          max="2"
          step="0.1"
          :placeholder="labels.notSent"
          :disabled="!!state.samplingBlock"
          :value="modelValue.temperature ?? ''"
          @input="update({ temperature: numberOrNull($event) })"
        />
      </div>
      <p v-if="state.samplingBlock" class="note">
        {{
          state.samplingBlock === 'unsupported'
            ? labels.samplingUnsupported
            : labels.samplingThinking
        }}
      </p>
    </div>

    <div class="field">
      <label for="ms-top-p">{{ labels.topP }}</label>
      <input
        id="ms-top-p"
        class="input num"
        type="number"
        min="0"
        max="1"
        step="0.05"
        :placeholder="labels.notSent"
        :disabled="!!state.samplingBlock"
        :value="modelValue.top_p ?? ''"
        @input="update({ top_p: numberOrNull($event) })"
      />
    </div>

    <div class="field">
      <label for="ms-top-k">{{ labels.topK }}</label>
      <input
        id="ms-top-k"
        class="input num"
        type="number"
        min="1"
        step="1"
        :placeholder="labels.notSent"
        :disabled="!!state.topKBlock"
        :value="modelValue.top_k ?? ''"
        @input="update({ top_k: numberOrNull($event) })"
      />
      <span v-if="state.topKBlock === 'unsupported' && !state.samplingBlock" class="meta">
        {{ labels.topKUnsupported }}
      </span>
    </div>

    <div class="field">
      <label for="ms-thinking">{{ labels.thinking }}</label>
      <select
        id="ms-thinking"
        class="select"
        :value="modelValue.thinking"
        @change="selectThinking(($event.target as HTMLSelectElement).value as ThinkingMode)"
      >
        <option v-for="mode in state.thinkingModes" :key="mode" :value="mode">
          {{ mode === 'default' ? thinkingDefaultLabel : labels.thinkingModes[mode] }}
        </option>
      </select>
      <input
        v-if="state.budgetVisible"
        class="input num"
        type="number"
        :min="current.min_thinking_budget"
        step="512"
        :aria-label="labels.thinkingBudget"
        :value="modelValue.thinking_budget ?? ''"
        @input="update({ thinking_budget: numberOrNull($event) })"
      />
      <span class="meta">{{ thinkingHint }}</span>
      <p v-if="state.budgetError" class="note note--fail">
        {{ state.budgetError === 'tooLow' ? labels.budgetTooLow : labels.budgetNotBelowMax }}
      </p>
    </div>

    <div class="field">
      <label for="ms-effort">{{ labels.effort }}</label>
      <select
        id="ms-effort"
        class="select"
        :disabled="!state.effortLevels.length"
        :value="modelValue.effort ?? ''"
        @change="selectEffort(($event.target as HTMLSelectElement).value)"
      >
        <option value="">
          {{
            state.effortLevels.length
              ? `${labels.effortDefault} (${current.default_effort})`
              : labels.effortUnsupported
          }}
        </option>
        <option v-for="level in state.effortLevels" :key="level" :value="level">
          {{ level }}
        </option>
      </select>
      <span class="meta">
        {{ state.effortLevels.length ? labels.effortHint : labels.effortNone }}
      </span>
      <p v-if="state.thinkingOffEffortError" class="note note--fail">
        {{ labels.thinkingOffEffort }} {{ current.thinking_off_max_effort }}.
      </p>
    </div>

    <div class="field">
      <label for="ms-max-tokens">{{ labels.maxTokens }}</label>
      <input
        id="ms-max-tokens"
        class="input num"
        type="number"
        min="1"
        :max="state.maxTokensCap"
        step="1000"
        :value="modelValue.max_tokens"
        @input="update({ max_tokens: numberOrNull($event) ?? 0 })"
      />
      <span class="meta">
        {{ labels.maxTokensHint }} {{ state.maxTokensCap.toLocaleString('de-DE') }}
      </span>
      <p v-if="state.maxTokensError" class="note note--fail">{{ labels.maxTokensTooHigh }}</p>
      <p
        v-else-if="state.thinkingOn && modelValue.max_tokens < THINKING_MAX_TOKENS"
        class="note"
      >
        {{ labels.maxTokensThinking }}
      </p>
    </div>

    <div class="field">
      <span class="field__label">{{ labels.price }}</span>
      <p class="num price">
        {{ usd(current.input_usd_per_mtok) }} {{ labels.priceIn }} ·
        {{ usd(current.output_usd_per_mtok) }} {{ labels.priceOut }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.settings {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--s4);
}

.settings__head {
  display: flex;
  align-items: center;
}

.full {
  grid-column: 1 / -1;
}

.field {
  min-width: 0;
}

.field__label {
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.slider {
  display: flex;
  align-items: center;
  gap: var(--s3);
}

.slider input[type='range'] {
  flex: 1;
  min-width: 0;
  accent-color: var(--mark);
}

.slider .input {
  width: 76px;
}

.slider--off {
  opacity: 0.5;
}

.note {
  padding: 6px 10px;
  border-radius: var(--r-md);
  background: var(--warn-soft);
  color: var(--warn);
  font-size: var(--t-xs);
}

.note--fail {
  background: var(--fail-soft);
  color: var(--fail);
}

.price {
  padding-top: var(--s2);
  font-size: var(--t-sm);
  color: var(--ink-2);
}

@media (max-width: 920px) {
  .settings {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
