<script setup lang="ts">
/**
 * The quality gates, explained.
 *
 * Two modes off one component, because the content is the same thing twice: a
 * gate's rule and method are properties of the gate, and a run only adds what
 * that gate measured. With a run id the page joins the two; without one it is
 * the catalogue — what the system checks, before anyone has uploaded anything.
 *
 * The gates describe themselves through the API, so a threshold that moves in
 * a gate moves on this page with no edit here.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import type { GateDetail, GateSpec, RunOut } from '@/api/types'
import StatusPill from '@/components/StatusPill.vue'
import { t, tt } from '@/i18n'
import { useCatalogueStore } from '@/stores/catalogue'
import { useRunsStore } from '@/stores/runs'

const props = defineProps<{ id?: string }>()

const runs = useRunsStore()
const catalogue = useCatalogueStore()

const entries = ref<GateDetail[]>([])
const run = ref<RunOut | null>(null)
const loading = ref(true)
const findingsOnly = ref(false)
const open = ref<string | null>(null)

const scoped = computed(() => Boolean(props.id))

const visible = computed(() =>
  findingsOnly.value
    ? entries.value.filter((entry) => (entry.report?.violations.length ?? 0) > 0)
    : entries.value,
)

const counts = computed(() => {
  const out = { pass: 0, warn: 0, fail: 0, skipped: 0 }
  for (const entry of entries.value) {
    if (entry.report) out[entry.report.status] += 1
  }
  return out
})

function severityLabel(spec: GateSpec): string {
  return spec.severity === 'fail' ? t.gate.severityFail : t.gate.severityWarn
}

function rows(record: Record<string, unknown>): { key: string; text: string }[] {
  return Object.entries(record).map(([key, raw]) => ({ key, text: render(raw) }))
}

function render(raw: unknown): string {
  if (raw === null || raw === undefined) return t.common.none
  if (Array.isArray(raw)) return raw.map(render).join(', ') || t.common.none
  if (typeof raw === 'object') return JSON.stringify(raw)
  if (typeof raw === 'number') return raw.toLocaleString('de-DE', { maximumFractionDigits: 4 })
  return String(raw)
}

function toggle(id: string): void {
  open.value = open.value === id ? null : id
}

async function load(): Promise<void> {
  loading.value = true
  try {
    await catalogue.load()
    if (props.id) {
      ;[run.value, entries.value] = await Promise.all([
        runs.get(props.id),
        runs.gateDetail(props.id),
      ])
    } else {
      run.value = null
      entries.value = catalogue.gates.map((spec) => ({ spec, report: null }))
    }
  } finally {
    loading.value = false
  }
}

watch(() => props.id, load)
onMounted(load)
</script>

<template>
  <div class="page">
    <header class="head">
      <div class="grow">
        <RouterLink
          v-if="scoped"
          :to="{ name: 'run', params: { id: props.id } }"
          class="eyebrow back"
        >
          ← {{ run?.document_title || t.run.title }}
        </RouterLink>
        <p v-else class="eyebrow">{{ t.app.name }}</p>
        <h1 class="h-page">{{ scoped ? t.gate.detailTitle : t.gate.catalogueTitle }}</h1>
        <p class="muted lead">{{ scoped ? t.gate.detailLead : t.gate.catalogueLead }}</p>
      </div>

      <div v-if="scoped" class="row wrap">
        <span v-if="counts.fail" class="badge badge--fail num">
          {{ counts.fail }} {{ t.gate.status.fail }}
        </span>
        <span v-if="counts.warn" class="badge badge--warn num">
          {{ counts.warn }} {{ t.gate.status.warn }}
        </span>
        <span class="badge badge--pass num">{{ counts.pass }} {{ t.gate.status.pass }}</span>
        <span v-if="counts.skipped" class="badge badge--idle num">
          {{ counts.skipped }} {{ t.gate.status.skipped }}
        </span>
      </div>
    </header>

    <label v-if="scoped" class="toggle">
      <input v-model="findingsOnly" type="checkbox" />
      {{ t.gate.filterFindings }}
    </label>

    <p v-if="loading" class="muted pad">{{ t.common.loading }}</p>

    <ul v-else class="gates">
      <li
        v-for="entry in visible"
        :key="entry.spec.id"
        class="gate card"
        :class="`gate--${entry.report?.status ?? 'none'}`"
      >
        <button
          class="gate__head"
          :aria-expanded="open === entry.spec.id"
          @click="toggle(entry.spec.id)"
        >
          <span class="gate__id num">{{ entry.spec.id }}</span>
          <span class="gate__title grow">
            <span class="gate__name">{{ tt(`gate.${entry.spec.id}`, entry.spec.name) }}</span>
            <span class="gate__rule">{{ entry.spec.rule }}</span>
          </span>
          <span v-if="entry.report?.violations.length" class="gate__count meta">
            {{ entry.report.violations.length }}
          </span>
          <StatusPill v-if="entry.report" :status="entry.report.status" kind="gate" />
          <span v-else class="badge badge--idle">{{ severityLabel(entry.spec) }}</span>
          <span class="chev" aria-hidden="true">{{ open === entry.spec.id ? '−' : '+' }}</span>
        </button>

        <div v-if="open === entry.spec.id" class="gate__body">
          <section class="block">
            <p class="eyebrow">{{ t.gate.method }}</p>
            <p class="prose method">{{ entry.spec.method }}</p>
          </section>

          <div class="grid2">
            <section class="block">
              <p class="eyebrow">{{ t.gate.severity }}</p>
              <p class="small">
                {{ severityLabel(entry.spec) }} ·
                {{ entry.spec.uses_model ? t.gate.usesModel : t.gate.deterministic }}
              </p>
              <p v-if="entry.spec.inspects.length" class="small muted">
                {{ t.gate.inspects }}: {{ entry.spec.inspects.join(', ') }}
              </p>
              <p v-if="entry.spec.skip_condition" class="small muted">
                {{ t.gate.skipCondition }}: {{ entry.spec.skip_condition }}
              </p>
            </section>

            <section v-if="rows(entry.spec.thresholds).length" class="block">
              <p class="eyebrow">{{ t.gate.thresholds }}</p>
              <ul class="kv">
                <li v-for="row in rows(entry.spec.thresholds)" :key="row.key">
                  <span class="kv__key">{{ row.key }}</span>
                  <span class="kv__value num">{{ row.text }}</span>
                </li>
              </ul>
            </section>
          </div>

          <section v-if="scoped" class="block">
            <p class="eyebrow">{{ t.gate.measurements }}</p>
            <p v-if="!entry.report" class="muted small">{{ t.gate.noMeasurements }}</p>
            <ul v-else-if="rows(entry.report.measurements).length" class="kv kv--wide">
              <li v-for="row in rows(entry.report.measurements)" :key="row.key">
                <span class="kv__key">{{ row.key }}</span>
                <span class="kv__value num">{{ row.text }}</span>
              </li>
            </ul>
            <p v-else class="muted small">{{ t.common.none }}</p>
          </section>

          <section v-if="entry.report" class="block">
            <p class="eyebrow">{{ t.gate.violations }}</p>
            <p v-if="entry.report.skip_reason" class="muted small">
              {{ t.gate.skipReason }}: {{ entry.report.skip_reason }}
            </p>
            <p v-else-if="!entry.report.violations.length" class="muted small">
              {{ t.gate.noViolations }}
            </p>
            <ul v-else class="findings">
              <li
                v-for="(violation, index) in entry.report.violations"
                :key="index"
                class="finding"
              >
                <span v-if="violation.target_id" class="meta mono">{{ violation.target_id }}</span>
                <span class="small">{{ violation.message }}</span>
              </li>
            </ul>
          </section>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.page {
  padding: var(--s6);
  max-width: 1100px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s4);
}

.head {
  display: flex;
  align-items: flex-end;
  gap: var(--s5);
  flex-wrap: wrap;
}

.back {
  display: inline-block;
  margin-bottom: 6px;
  text-decoration: none;
  color: var(--ink-3);
}

.lead {
  margin-top: 6px;
  font-size: var(--t-sm);
  max-width: 76ch;
}

.toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--t-sm);
  color: var(--ink-2);
}

.gates {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s2);
}

.gate {
  border-left: 3px solid var(--rule-strong);
  overflow: hidden;
}

.gate--pass {
  border-left-color: var(--pass);
}
.gate--warn {
  border-left-color: var(--warn);
}
.gate--fail {
  border-left-color: var(--fail);
}
.gate--skipped {
  border-left-color: var(--rule-strong);
}

.gate__head {
  display: flex;
  align-items: center;
  gap: var(--s3);
  width: 100%;
  padding: var(--s3) var(--s4);
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.gate__head:hover {
  background: var(--chrome);
}

.gate__id {
  font-family: var(--mono);
  font-size: var(--t-sm);
  color: var(--ink-3);
  width: 26px;
}

.gate__title {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.gate__name {
  font-size: var(--t-md);
  font-weight: 600;
  letter-spacing: -0.01em;
}

.gate__rule {
  font-size: var(--t-sm);
  color: var(--ink-2);
  line-height: 1.45;
}

.gate__count {
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--sunk);
}

.chev {
  font-family: var(--mono);
  color: var(--ink-3);
  width: 12px;
  text-align: center;
}

.gate__body {
  display: grid;
  gap: var(--s4);
  padding: 0 var(--s4) var(--s4) 46px;
}

.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--s4);
  align-items: start;
}

.block {
  display: grid;
  gap: var(--s2);
}

.method {
  font-size: var(--t-sm);
  color: var(--ink-2);
  max-width: 88ch;
  margin: 0;
}

.kv {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 1px;
}

.kv--wide {
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 1px var(--s4);
}

.kv li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--s3);
  padding: 3px var(--s2);
  border-radius: var(--r-sm);
  font-size: var(--t-xs);
  background: var(--chrome);
}

.kv__key {
  font-family: var(--mono);
  color: var(--ink-3);
}

.kv__value {
  color: var(--ink);
  text-align: right;
  overflow-wrap: anywhere;
}

.findings {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s2);
}

.finding {
  display: grid;
  gap: 2px;
  padding: var(--s2) var(--s3);
  border-radius: var(--r-sm);
  background: var(--chrome);
}

.mono {
  font-family: var(--mono);
}

.small {
  font-size: var(--t-sm);
}

.pad {
  padding: var(--s7);
  text-align: center;
}

@media (max-width: 760px) {
  .grid2 {
    grid-template-columns: 1fr;
  }
  .gate__body {
    padding-left: var(--s4);
  }
}
</style>
