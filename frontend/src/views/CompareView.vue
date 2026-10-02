<script setup lang="ts">
/**
 * Two to four collected outputs side by side, from any folders and
 * experiments, with what was sent for each. Rows that differ are marked, so
 * the reason two answers differ sits next to them; status and note are set
 * right here. The outputs are the `ids` query, so a comparison can be
 * bookmarked or sent on.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import type { ExperimentOutput, ExperimentSummary } from '@/api/types'
import ArtifactView from '@/components/ArtifactView.vue'
import OutputCard, { type OutputView } from '@/components/experiments/OutputCard.vue'
import OutputDecision from '@/components/experiments/OutputDecision.vue'
import OutputText from '@/components/experiments/OutputText.vue'
import { listAllOutputs, listExperiments } from '@/experiments/api'
import { type CompareRow, compareTable } from '@/experiments/compare'
import { outputWarnings } from '@/experiments/outputMeta'
import { presenterFor } from '@/experiments/presenters'
import { readRevealed } from '@/experiments/verbalized_sampling/vs'
import { t } from '@/i18n'

type Output = ExperimentOutput<unknown>

const MAX = 4
const labels = t.collection
const route = useRoute()
const router = useRouter()
const revealed = readRevealed()

const outputs = ref<Output[]>([])
const experiments = ref<ExperimentSummary[]>([])
const missing = ref<string[]>([])
const loading = ref(true)
const error = ref('')
const onlyDifferences = ref(false)
const view = ref<OutputView>('text')

const ids = computed(() => {
  const raw = route.query.ids
  const value = Array.isArray(raw) ? raw[0] : raw
  return [...new Set((value ?? '').split(',').filter(Boolean))].slice(0, MAX)
})

const titles = computed(() => new Map(experiments.value.map((e) => [e.key, e.title])))

function experimentTitle(output: Output): string {
  return titles.value.get(output.experiment_key) ?? labels.unknownExperiment
}

function present(output: Output) {
  return presenterFor(output.experiment_key, revealed)
}

const table = computed(() => compareTable(outputs.value, experimentTitle, revealed))

function visible(rows: CompareRow[]): CompareRow[] {
  return onlyDifferences.value ? rows.filter((row) => row.differs) : rows
}

function summary(row: CompareRow, index: number): string {
  if (!row.differs) return labels.same
  if (index === 0) return labels.fullText
  return row.values[index] === row.values[0] ? labels.asFirst : labels.differs
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [page, summaries] = await Promise.all([
      ids.value.length ? listAllOutputs({ ids: ids.value, limit: MAX }) : null,
      experiments.value.length ? experiments.value : listExperiments(),
    ])
    experiments.value = summaries
    const byId = new Map((page?.items ?? []).map((item) => [item.id, item]))
    outputs.value = ids.value.flatMap((id) => byId.get(id) ?? [])
    missing.value = ids.value.filter((id) => !byId.has(id))
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    loading.value = false
  }
}

function replace(updated: Output): void {
  outputs.value = outputs.value.map((item) => (item.id === updated.id ? updated : item))
}

function drop(id: string): void {
  const next = ids.value.filter((item) => item !== id)
  void router.replace({ query: { ...route.query, ids: next.join(',') } })
}

watch(ids, load)
onMounted(load)
</script>

<template>
  <div class="page">
    <header class="head">
      <div class="grow">
        <p class="eyebrow crumbs">
          <RouterLink :to="{ name: 'experiments' }">{{ t.experiments.title }}</RouterLink>
          /
          <RouterLink :to="{ name: 'experiments', query: { tab: 'collection' } }">
            {{ labels.tab }}
          </RouterLink>
          / {{ labels.compare }}
        </p>
        <h1 class="h-page">{{ labels.compareTitle }}</h1>
        <p class="muted lead">{{ labels.compareLead }}</p>
      </div>
      <div class="side">
        <label class="meta check">
          <input v-model="onlyDifferences" type="checkbox" /> {{ labels.onlyDifferences }}
        </label>
        <span class="seg" role="group">
          <button :aria-pressed="view === 'text'" @click="view = 'text'">
            {{ t.experiments.text }}
          </button>
          <button :aria-pressed="view === 'json'" @click="view = 'json'">
            {{ t.experiments.json }}
          </button>
        </span>
        <RouterLink class="btn" :to="{ name: 'experiments', query: { tab: 'collection' } }">
          ← {{ labels.back }}
        </RouterLink>
      </div>
    </header>

    <div class="body">
      <p v-if="error" class="banner" role="alert">{{ error }}</p>
      <p v-if="missing.length && !loading" class="notice" role="status">
        {{ labels.missing }} {{ missing.length }}
      </p>
      <p v-if="loading" class="muted">{{ t.common.loading }}</p>
      <p v-else-if="outputs.length < 2" class="empty">{{ labels.tooFew }}</p>

      <template v-else>
        <div class="tablewrap">
          <table class="set">
            <thead>
              <tr>
                <th scope="col"><span class="sr-only">{{ labels.setting }}</span></th>
                <th v-for="(output, index) in outputs" :key="output.id" scope="col">
                  <span class="colhead">
                    <span class="colnum">{{ index + 1 }}</span>
                    <span class="truncate">{{ present(output).titleOf(output) }}</span>
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr class="group">
                <th scope="rowgroup" :colspan="outputs.length + 1">{{ labels.sent }}</th>
              </tr>
              <tr
                v-for="row in visible(table.sent)"
                :key="row.key"
                :class="{ diff: row.differs }"
                :data-row="row.key"
              >
                <th scope="row">
                  {{ row.label }}<span v-if="row.differs" aria-hidden="true"> ≠</span>
                </th>
                <td v-for="(value, index) in row.values" :key="index">
                  <details v-if="row.long">
                    <summary>{{ summary(row, index) }}</summary>
                    <pre>{{ value }}</pre>
                  </details>
                  <template v-else>{{ value }}</template>
                </td>
              </tr>
            </tbody>
            <tbody v-if="!onlyDifferences">
              <tr class="group">
                <th scope="rowgroup" :colspan="outputs.length + 1">{{ labels.result }}</th>
              </tr>
              <tr v-for="row in table.result" :key="row.key" :data-row="row.key">
                <th scope="row">{{ row.label }}</th>
                <td v-for="(value, index) in row.values" :key="index">{{ value }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="cols">
          <OutputCard
            v-for="(output, index) in outputs"
            :key="output.id"
            :title="present(output).titleOf(output)"
            :payload="present(output).payloadOf(output)"
            :text="output.text"
            :badges="[{ text: experimentTitle(output) }, ...present(output).badgesOf(output)]"
            :warnings="outputWarnings(output.meta)"
            :view="view"
          >
            <template #lead>
              <span class="colnum">{{ index + 1 }}</span>
            </template>
            <template v-if="present(output).artifactOf(output)" #text>
              <ArtifactView
                :model="present(output).artifactOf(output)!.model"
                :payload="present(output).artifactOf(output)!.payload"
                mode="text"
              />
            </template>
            <template v-else #text>
              <OutputText :payload="present(output).payloadOf(output)" :text="output.text" />
            </template>
            <template #foot>
              <OutputDecision :output="output" @updated="replace" />
              <span class="grow" />
              <button
                class="btn btn--sm btn--ghost"
                :disabled="outputs.length <= 2"
                @click="drop(output.id)"
              >
                {{ labels.removeColumn }}
              </button>
            </template>
          </OutputCard>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  grid-template-rows: auto 1fr;
  height: 100%;
  min-height: 0;
}

.head {
  display: flex;
  align-items: flex-end;
  gap: var(--s5);
  flex-wrap: wrap;
  padding: var(--s5) var(--s6) var(--s4);
  border-bottom: 1px solid var(--rule);
}

.crumbs a {
  color: inherit;
  text-decoration: none;
}

.crumbs a:hover {
  color: var(--mark);
}

.lead {
  max-width: 66ch;
  margin-top: var(--s2);
  font-size: var(--t-sm);
}

.side {
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex-wrap: wrap;
}

a.btn {
  text-decoration: none;
}

.check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.check input {
  accent-color: var(--mark);
  margin: 0;
}

.seg {
  display: inline-flex;
  border: 1px solid var(--rule-strong);
  border-radius: var(--r-md);
  overflow: hidden;
}

.seg button {
  border: 0;
  background: var(--card);
  padding: 3px 10px;
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.06em;
  color: var(--ink-2);
  cursor: pointer;
}

.seg button + button {
  border-left: 1px solid var(--rule-strong);
}

.seg button[aria-pressed='true'] {
  background: var(--ink);
  color: var(--chrome);
}

.body {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
  min-height: 0;
  min-width: 0;
  overflow: auto;
  padding: var(--s4) var(--s6) var(--s7);
}

.tablewrap {
  overflow-x: auto;
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  background: var(--card);
}

.set {
  width: 100%;
  min-width: 640px;
  border-collapse: separate;
  border-spacing: 0;
  font-size: var(--t-sm);
}

.set th,
.set td {
  padding: 6px 10px;
  border-bottom: 1px solid var(--rule);
  text-align: left;
  vertical-align: top;
}

.set thead th {
  background: var(--chrome);
  font-weight: 620;
}

.set tbody th[scope='row'] {
  position: sticky;
  left: 0;
  width: 150px;
  background: var(--card);
  font-family: var(--mono);
  font-size: var(--t-xs);
  font-weight: 500;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-3);
  white-space: nowrap;
}

.set td {
  font-family: var(--mono);
  font-size: 0.78rem;
  overflow-wrap: anywhere;
}

.set tr.diff td {
  background: var(--mark-soft);
  color: var(--mark-deep);
}

.set tr.diff th[scope='row'] {
  color: var(--mark-deep);
}

.set tr.group th {
  background: var(--chrome);
  font-family: var(--mono);
  font-size: var(--t-xs);
  font-weight: 500;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--ink-2);
}

.set summary {
  cursor: pointer;
  color: var(--ink-2);
}

.set pre {
  max-height: 180px;
  margin: 6px 0 0;
  overflow: auto;
  padding: 6px 8px;
  border-radius: var(--r-sm);
  background: var(--sunk);
  color: var(--ink);
  font-family: var(--mono);
  font-size: 0.72rem;
  white-space: pre-wrap;
}

.colhead {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.colnum {
  display: inline-grid;
  flex: none;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--ink);
  color: var(--chrome);
  font-family: var(--mono);
  font-size: var(--t-xs);
}

.cols {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(300px, 1fr);
  gap: var(--s3);
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  padding-bottom: 4px;
}

.cols > :deep(.out) {
  scroll-snap-align: start;
}

.banner,
.notice {
  padding: var(--s3) var(--s4);
  border-radius: var(--r-md);
}

.banner {
  background: var(--fail-soft);
  color: var(--fail);
}

.notice {
  background: var(--warn-soft);
  color: var(--warn);
}

.empty {
  padding: var(--s6);
  text-align: center;
  border: 1px dashed var(--rule-strong);
  border-radius: var(--r-lg);
  color: var(--ink-3);
  font-size: var(--t-sm);
}

@media (max-width: 920px) {
  .head,
  .body {
    padding: var(--s4);
  }

  .cols {
    grid-auto-columns: minmax(85%, 1fr);
  }
}
</style>
