<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import type { AudienceSpec, DocumentBudget, DocumentSummary } from '@/api/types'
import { fill, t } from '@/i18n'
import { estimateSeries, MAX_EPISODES, MIN_EPISODES } from '@/series/estimate'
import { useCatalogueStore } from '@/stores/catalogue'
import { useDocumentsStore } from '@/stores/documents'
import { useRunsStore } from '@/stores/runs'
import { useSeriesStore } from '@/stores/series'

const props = defineProps<{ id: string }>()

const documents = useDocumentsStore()
const catalogue = useCatalogueStore()
const runs = useRunsStore()
const series = useSeriesStore()
const router = useRouter()

/** Nodes that pause for a person; a series cannot run such a flow yet. */
const PAUSING = ['human_feedback']

const document = ref<DocumentSummary | null>(null)
const budget = ref<DocumentBudget | null>(null)
const scope = ref<'one' | 'series'>('one')
const flowId = ref('baseline_v0')
const planFlowId = ref('series_plan_v0')
const formatId = ref('two_host_dialogue')
const targetMinutes = ref(15)
/** `null` follows the suggestion; a number is the reviewer's own count. */
const ownCount = ref<number | null>(null)
const hint = ref('')
const audience = ref<AudienceSpec>({
  description: '',
  prior_knowledge: '',
  listening_context: '',
  desired_outcome: '',
})
const busy = ref(false)
const error = ref('')

const episodeFlows = computed(() =>
  catalogue.flows.filter(
    (flow) =>
      (flow.purpose ?? 'episode') === 'episode' &&
      (scope.value === 'one' || !flow.nodes.some((node) => PAUSING.includes(node))),
  ),
)
const planFlows = computed(() => catalogue.flows.filter((flow) => flow.purpose === 'series_plan'))

const estimate = computed(() =>
  budget.value
    ? estimateSeries(budget.value.max_supportable_minutes, targetMinutes.value, ownCount.value)
    : null,
)
const shownCount = computed(() => estimate.value?.episodes ?? ownCount.value ?? MIN_EPISODES)
const supportable = computed(() => budget.value?.max_supportable_minutes ?? 0)

function minutesText(value: number): string {
  return value.toLocaleString('de-DE', { maximumFractionDigits: 1 })
}

const estimateNote = computed(() => {
  const e = estimate.value
  if (!e) return ''
  const values = {
    n: e.episodes,
    minutes: targetMinutes.value,
    total: e.episodes * targetMinutes.value,
    max: minutesText(supportable.value),
    per: minutesText(e.perEpisode),
  }
  switch (e.reason) {
    case 'tooFew':
      return fill(t.series.tooFewForSeries, values)
    case 'tooThin':
      return fill(t.series.tooThin, values)
    case 'more':
      return fill(t.series.moreThanBudget, values)
    case 'fewer':
      return fill(t.series.fewerThanSuggested, values)
    default:
      return ''
  }
})

const singleNote = computed(() => {
  if (!budget.value) return ''
  const max = minutesText(supportable.value)
  if (targetMinutes.value > supportable.value) return fill(t.series.singleClamped, { max })
  return fill(t.series.single, {
    max,
    minutes: targetMinutes.value,
    share: Math.round((targetMinutes.value / Math.max(supportable.value, 0.01)) * 100),
  })
})

function step(delta: number): void {
  ownCount.value = Math.max(MIN_EPISODES, Math.min(MAX_EPISODES, shownCount.value + delta))
}

function useSuggestion(): void {
  ownCount.value = null
}

function useOwn(): void {
  if (ownCount.value === null) ownCount.value = shownCount.value
}

async function submit(): Promise<void> {
  busy.value = true
  error.value = ''
  const audienceSpec = audience.value.description.trim() ? audience.value : undefined
  try {
    if (scope.value === 'series') {
      const created = await series.create({
        document_id: props.id,
        flow_id: flowId.value,
        plan_flow_id: planFlowId.value,
        format_id: formatId.value,
        episodes: ownCount.value,
        minutes_per_episode: targetMinutes.value,
        hint: hint.value.trim() || undefined,
        audience_spec: audienceSpec,
      })
      await router.push({ name: 'series', params: { id: created.id } })
      return
    }
    const run = await runs.create({
      document_id: props.id,
      flow_id: flowId.value,
      format_id: formatId.value,
      target_minutes: targetMinutes.value,
      audience_spec: audienceSpec,
    })
    await router.push({ name: 'run', params: { id: run.id } })
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : t.errors.generic
  } finally {
    busy.value = false
  }
}

watch(scope, () => {
  if (!episodeFlows.value.some((flow) => flow.id === flowId.value)) {
    flowId.value = episodeFlows.value[0]?.id ?? flowId.value
  }
})

onMounted(async () => {
  document.value = await documents.get(props.id)
  const format = catalogue.formats.find((f) => f.id === formatId.value)
  if (format) targetMinutes.value = format.target_minutes
  budget.value = await series.budget(props.id).catch(() => null)
})
</script>

<template>
  <div class="page">
    <header>
      <RouterLink :to="{ name: 'document', params: { id: props.id } }" class="eyebrow back">
        ← {{ document?.title || document?.filename || t.documents.title }}
      </RouterLink>
      <h1 class="h-page">{{ t.run.newTitle }}</h1>
    </header>

    <form class="sheet form" @submit.prevent="submit">
      <div class="field">
        <span class="label">{{ t.run.scope }}</span>
        <div class="toggle" role="group" :aria-label="t.run.scope">
          <button
            type="button"
            data-scope="one"
            :aria-pressed="scope === 'one'"
            @click="scope = 'one'"
          >
            {{ t.run.scopeOne }}
          </button>
          <button
            type="button"
            data-scope="series"
            :aria-pressed="scope === 'series'"
            @click="scope = 'series'"
          >
            {{ t.run.scopeSeries }}
          </button>
        </div>
      </div>

      <div class="pair">
        <div class="field">
          <label for="flow">{{ scope === 'series' ? t.series.flowPerEpisode : t.run.flow }}</label>
          <select id="flow" v-model="flowId" class="select">
            <option v-for="flow in episodeFlows" :key="flow.id" :value="flow.id">
              {{ flow.id }} v{{ flow.version }}
            </option>
          </select>
          <p class="hint">
            {{ catalogue.flows.find((f) => f.id === flowId)?.description }}
          </p>
        </div>

        <div v-if="scope === 'series'" class="field">
          <label for="planflow">{{ t.series.planner }}</label>
          <select id="planflow" v-model="planFlowId" class="select">
            <option v-for="flow in planFlows" :key="flow.id" :value="flow.id">
              {{ flow.id }} v{{ flow.version }}
            </option>
          </select>
        </div>

        <div class="field">
          <label for="format">{{ t.run.format }}</label>
          <select id="format" v-model="formatId" class="select">
            <option v-for="format in catalogue.formats" :key="format.id" :value="format.id">
              {{ format.name }}
            </option>
          </select>
          <p class="hint">
            {{
              catalogue.formats
                .find((f) => f.id === formatId)
                ?.speakers.map((s) => `${s.name} — ${s.role}`)
                .join(' · ')
            }}
          </p>
        </div>
      </div>

      <div class="pair">
        <div v-if="scope === 'series'" class="field">
          <span class="label">{{ t.series.count }}</span>
          <div class="row">
            <div class="toggle" role="group" :aria-label="t.series.count">
              <button
                type="button"
                data-count="auto"
                :aria-pressed="ownCount === null"
                @click="useSuggestion"
              >
                {{ t.series.countSuggested }}
              </button>
              <button
                type="button"
                data-count="own"
                :aria-pressed="ownCount !== null"
                @click="useOwn"
              >
                {{ t.series.countOwn }}
              </button>
            </div>
            <span class="stepper">
              <button
                type="button"
                class="btn btn--sm"
                data-step="-1"
                :aria-label="t.series.countLess"
                :disabled="shownCount <= MIN_EPISODES"
                @click="step(-1)"
              >
                −
              </button>
              <output class="num count__out" data-count-out>{{ shownCount }}</output>
              <button
                type="button"
                class="btn btn--sm"
                data-step="1"
                :aria-label="t.series.countMore"
                :disabled="shownCount >= MAX_EPISODES"
                @click="step(1)"
              >
                +
              </button>
            </span>
          </div>
          <p class="hint">
            {{
              ownCount === null
                ? t.series.countHintSuggested
                : fill(t.series.countHintOwn, { n: estimate?.suggested ?? MIN_EPISODES })
            }}
          </p>
        </div>

        <div class="field minutes">
          <label for="minutes">
            {{ scope === 'series' ? t.series.minutesPerEpisode : t.run.targetMinutes }}
          </label>
          <div class="row">
            <input
              id="minutes"
              v-model.number="targetMinutes"
              class="input"
              type="range"
              min="3"
              max="60"
              step="1"
            />
            <output class="num minutes__out">{{ targetMinutes }}</output>
          </div>
        </div>
      </div>

      <div
        v-if="budget"
        class="supports"
        :class="scope === 'series' && estimate ? `supports--${estimate.tone}` : ''"
        data-supports
        aria-live="polite"
      >
        <template v-if="scope === 'series' && estimate">
          <span>
            {{ fill(t.series.supports, { minutes: minutesText(supportable) }) }} ·
            {{ ownCount === null ? t.series.suggestion : t.series.chosen }}:
            <b>{{
              fill(estimate.episodes === 1 ? t.series.oneEpisode : t.series.episodesOf, {
                n: estimate.episodes,
                minutes: minutesText(estimate.perEpisode),
              })
            }}</b>
            · {{ fill(t.series.uses, { used: minutesText(estimate.used), total: minutesText(supportable) }) }}
            ·
            {{
              fill(t.series.estimate, {
                low: estimate.low.toFixed(2).replace('.', ','),
                high: estimate.high.toFixed(2).replace('.', ','),
              })
            }}
          </span>
          <span v-if="estimateNote">{{ estimateNote }}</span>
          <span class="cover" aria-hidden="true">
            <i
              v-for="index in estimate.episodes"
              :key="index"
              :class="{ short: estimate.tone !== 'ok' }"
              :style="{ flex: estimate.perEpisode }"
            />
            <i
              v-if="supportable - estimate.used > 0.5"
              class="rest"
              :style="{ flex: supportable - estimate.used }"
            />
          </span>
        </template>
        <span v-else>{{ singleNote }}</span>
      </div>

      <template v-if="scope === 'series'">
        <div class="field">
          <label for="hint">{{ t.series.hint }}</label>
          <input id="hint" v-model="hint" class="input" :placeholder="t.series.hintPlaceholder" />
        </div>
      </template>

      <fieldset class="audience">
        <legend class="eyebrow">{{ t.run.audience }}</legend>
        <div class="field">
          <label for="audience-desc">{{ t.run.audienceDescription }}</label>
          <textarea
            id="audience-desc"
            v-model="audience.description"
            class="textarea"
            rows="3"
          />
        </div>
        <div class="pair">
          <div class="field">
            <label for="prior">{{ t.run.priorKnowledge }}</label>
            <textarea id="prior" v-model="audience.prior_knowledge" class="textarea" rows="4" />
          </div>
          <div class="field">
            <label for="outcome">{{ t.run.desiredOutcome }}</label>
            <textarea id="outcome" v-model="audience.desired_outcome" class="textarea" rows="4" />
          </div>
        </div>
        <div class="field">
          <label for="context">{{ t.run.listeningContext }}</label>
          <input id="context" v-model="audience.listening_context" class="input" />
        </div>
      </fieldset>

      <p v-if="error" class="error" role="alert">{{ error }}</p>

      <div class="actions">
        <RouterLink class="btn" :to="{ name: 'document', params: { id: props.id } }">
          {{ t.common.cancel }}
        </RouterLink>
        <button
          class="btn btn--primary"
          type="submit"
          data-submit
          :disabled="busy || (scope === 'series' && estimate?.tone === 'fail')"
        >
          {{ busy ? t.run.starting : scope === 'series' ? t.series.start : t.run.start }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.page {
  padding: var(--s6);
  max-width: 780px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s5);
}

.back {
  display: inline-block;
  margin-bottom: 6px;
  text-decoration: none;
  color: var(--ink-3);
}

.form {
  display: flex;
  flex-direction: column;
  gap: var(--s5);
}

.pair {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--s4);
}

.label {
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.hint {
  font-size: var(--t-xs);
  color: var(--ink-3);
  line-height: 1.4;
}

.block {
  display: block;
}

.toggle {
  display: inline-flex;
  align-self: flex-start;
  border: 1px solid var(--rule-strong);
  border-radius: var(--r-md);
  overflow: hidden;
}

.toggle button {
  border: 0;
  background: var(--card);
  padding: 5px 12px;
  font-size: var(--t-sm);
  cursor: pointer;
  color: var(--ink-2);
}

.toggle button + button {
  border-left: 1px solid var(--rule-strong);
}

.toggle button[aria-pressed='true'] {
  background: var(--ink);
  color: var(--chrome);
}

.stepper {
  display: inline-flex;
  align-items: center;
  gap: var(--s2);
}

.count__out,
.minutes__out {
  font-size: var(--t-xl);
  font-weight: 600;
  min-width: 2.5ch;
  text-align: right;
  letter-spacing: -0.02em;
}

.count__out {
  min-width: 1.6ch;
  text-align: center;
}

.supports {
  padding: var(--s3) var(--s4);
  border-radius: var(--r-md);
  background: var(--chrome);
  border: 1px solid var(--rule);
  font-size: var(--t-sm);
  color: var(--ink-2);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.supports b {
  color: var(--ink);
}

.supports--warn {
  background: var(--warn-soft);
  border-color: var(--warn);
  color: var(--warn);
}

.supports--fail {
  background: var(--fail-soft);
  border-color: var(--fail);
  color: var(--fail);
}

.cover {
  display: flex;
  gap: 2px;
  height: 8px;
  margin-top: 4px;
}

.cover i {
  border-radius: 2px;
  background: var(--mark);
}

.cover i.short {
  background: var(--warn);
}

.cover i.rest {
  background: var(--rule-strong);
}

.audience {
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  padding: var(--s4);
  display: grid;
  gap: var(--s4);
}

.audience legend {
  padding: 0 6px;
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--s3);
}

.actions .btn {
  text-decoration: none;
}

.error {
  color: var(--fail);
  font-size: var(--t-sm);
}

@media (max-width: 640px) {
  .pair {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
