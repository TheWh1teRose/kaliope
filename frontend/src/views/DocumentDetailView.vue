<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import type { BlockOut, DocumentSummary, StructureOut } from '@/api/types'
import ConfidenceStrip from '@/components/ConfidenceStrip.vue'
import ModalDialog from '@/components/ModalDialog.vue'
import PageCanvas from '@/components/PageCanvas.vue'
import { t } from '@/i18n'
import { useCatalogueStore } from '@/stores/catalogue'
import { useDocumentsStore } from '@/stores/documents'

const props = defineProps<{ id: string }>()

const documents = useDocumentsStore()
const catalogue = useCatalogueStore()

const document = ref<DocumentSummary | null>(null)
const structure = ref<StructureOut | null>(null)
const page = ref(0)
const selectedBlockId = ref<string | null>(null)
const sectionFilter = ref<string>('')
const uncertainOnly = ref(false)
const relabelTarget = ref<BlockOut | null>(null)
const relabelZone = ref('')
const relabelNote = ref('')
const busy = ref(false)

const blocks = computed(() => {
  const all = structure.value?.blocks ?? []
  return all.filter((block) => {
    if (uncertainOnly.value && !block.zone_uncertain) return false
    if (sectionFilter.value && block.section_id !== sectionFilter.value) return false
    return true
  })
})

const pageSize = computed<[number, number]>(
  () => structure.value?.page_sizes?.[page.value] ?? [595, 842],
)

const selectedBlock = computed(
  () => structure.value?.blocks.find((b) => b.id === selectedBlockId.value) ?? null,
)

const zoneCounts = computed(() => document.value?.report?.zone_distribution ?? {})

function zoneLabel(zone: string): string {
  return catalogue.zoneSpec(zone)?.meaning ?? zone
}

function narratabilityLabel(zone: string): string {
  const spec = catalogue.zoneSpec(zone)
  if (!spec) return ''
  if (spec.context_only) return t.structure.contextOnly
  return spec.narratable ? t.structure.narratable : t.structure.notNarratable
}

function selectBlock(id: string): void {
  selectedBlockId.value = id
  const block = structure.value?.blocks.find((b) => b.id === id)
  if (block) page.value = block.page
}

function openRelabel(block: BlockOut): void {
  relabelTarget.value = block
  relabelZone.value = block.zone
  relabelNote.value = ''
}

async function saveRelabel(): Promise<void> {
  if (!relabelTarget.value || !structure.value) return
  busy.value = true
  try {
    await documents.relabel(props.id, relabelTarget.value.id, relabelZone.value, relabelNote.value)
    structure.value = await documents.structure(props.id)
    relabelTarget.value = null
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  document.value = await documents.get(props.id)
  if (document.value.parse_status === 'parsed') {
    structure.value = await documents.structure(props.id)
  }
})
</script>

<template>
  <div v-if="document" class="detail">
    <header class="detail__head">
      <div class="grow">
        <RouterLink :to="{ name: 'documents' }" class="eyebrow back">
          ← {{ t.documents.title }}
        </RouterLink>
        <h1 class="h-page truncate">{{ document.title || document.filename }}</h1>
        <p class="meta">
          {{ document.page_count }} {{ t.documents.pages }} · {{ document.language }} ·
          parse v{{ document.parse_version }}
        </p>
      </div>
      <div v-if="document.parse_status === 'parsed'" class="row wrap">
        <RouterLink class="btn" :to="{ name: 'bench', query: { document: document.id } }">
          {{ t.documents.openBench }}
        </RouterLink>
        <RouterLink class="btn btn--mark" :to="{ name: 'new-run', params: { id: document.id } }">
          {{ t.documents.newRun }}
        </RouterLink>
      </div>
    </header>

    <section v-if="document.report" class="report sheet">
      <div class="spread">
        <h2 class="h-section">{{ t.report.title }}</h2>
        <span
          class="badge"
          :class="`badge--${
            document.report.ingestion_confidence === 'high'
              ? 'pass'
              : document.report.ingestion_confidence === 'medium'
                ? 'warn'
                : 'fail'
          }`"
        >
          {{ t.report.confidence }}: {{ t.report[document.report.ingestion_confidence] }}
        </span>
      </div>

      <ConfidenceStrip :report="document.report" />

      <details class="why" open>
        <summary class="eyebrow">{{ t.report.why }}</summary>
        <ul class="reasons">
          <li v-for="(reason, index) in document.report.confidence_reasons" :key="index">
            {{ reason }}
          </li>
        </ul>
      </details>

      <details v-if="document.report.warnings.length" class="why">
        <summary class="eyebrow">
          {{ t.report.warnings }} ({{ document.report.warnings.length }})
        </summary>
        <ul class="reasons">
          <li v-for="(warning, index) in document.report.warnings" :key="index">{{ warning }}</li>
        </ul>
      </details>

      <div class="figures">
        <div v-for="(count, zone) in zoneCounts" :key="zone" class="figure">
          <span class="swatch" :style="{ background: `var(--zone-${zone})` }" />
          <span class="figure__label truncate">{{ zone }}</span>
          <span class="num figure__value">{{ count }}</span>
        </div>
      </div>
    </section>

    <section v-if="structure" class="workspace">
      <aside class="panel">
        <div class="panel__controls">
          <select v-model="sectionFilter" class="select">
            <option value="">{{ t.structure.allSections }}</option>
            <option v-for="section in structure.sections ?? []" :key="section.id" :value="section.id">
              {{ section.title || `${t.structure.sections} ${section.ordinal + 1}` }}
            </option>
          </select>
          <label class="toggle">
            <input v-model="uncertainOnly" type="checkbox" />
            {{ t.structure.uncertainOnly }}
          </label>
        </div>

        <p v-if="structure.objectives.length" class="objectives">
          <span class="eyebrow">{{ t.structure.objectives }}</span>
          <span v-for="(objective, index) in structure.objectives" :key="index" class="objective">
            {{ objective }}
          </span>
        </p>

        <ul v-if="blocks.length" class="blocks scroll">
          <li
            v-for="block in blocks"
            :key="block.id"
            class="block"
            :class="{ 'block--on': block.id === selectedBlockId }"
            :style="{ '--zone': `var(--zone-${block.zone})` }"
          >
            <button class="block__body" @click="selectBlock(block.id)">
              <span class="block__zone meta">
                {{ block.zone }}
                <em v-if="block.zone_uncertain" class="flagword">?</em>
                <em v-if="block.zone_overridden_from" class="flagword">
                  {{ t.structure.overridden }}
                </em>
              </span>
              <span class="block__text">{{ block.text.slice(0, 180) }}</span>
              <span class="meta">
                {{ t.common.page }} {{ block.page + 1 }} · {{ block.id }}
              </span>
            </button>
            <button class="btn btn--sm btn--ghost" @click="openRelabel(block)">
              {{ t.structure.relabel }}
            </button>
          </li>
        </ul>
        <p v-else class="muted pad">{{ t.structure.noBlocks }}</p>
      </aside>

      <div class="viewer">
        <div class="viewer__bar">
          <button class="btn btn--sm" :disabled="page === 0" @click="page = page - 1">←</button>
          <span class="num">
            {{ t.common.page }} {{ page + 1 }} {{ t.common.of }} {{ structure.page_count }}
          </span>
          <button
            class="btn btn--sm"
            :disabled="page >= structure.page_count - 1"
            @click="page = page + 1"
          >
            →
          </button>
          <span class="grow" />
          <span
            v-if="selectedBlock"
            class="badge badge--mark"
            :title="zoneLabel(selectedBlock.zone)"
          >
            {{ selectedBlock.zone }} · {{ narratabilityLabel(selectedBlock.zone) }}
          </span>
        </div>
        <PageCanvas
          class="scroll"
          :document-id="props.id"
          :page="page"
          :page-size="pageSize"
          :blocks="structure.blocks"
          :zones="catalogue.zones"
          :selected-block-id="selectedBlockId"
          @select-block="selectBlock"
        />
      </div>
    </section>

    <ModalDialog
      :open="Boolean(relabelTarget)"
      :title="t.structure.relabelTitle"
      :lead="t.structure.relabelLead"
      @close="relabelTarget = null"
    >
      <div class="stack">
        <p class="quote prose">{{ relabelTarget?.text.slice(0, 400) }}</p>
        <div class="field">
          <label for="zone">{{ t.structure.newZone }}</label>
          <select id="zone" v-model="relabelZone" class="select" data-autofocus>
            <option v-for="spec in catalogue.zones" :key="spec.zone" :value="spec.zone">
              {{ spec.zone }} — {{ spec.meaning }}
            </option>
          </select>
        </div>
        <div class="field">
          <label for="relabel-note">{{ t.review.note }}</label>
          <textarea id="relabel-note" v-model="relabelNote" class="textarea" rows="2" />
        </div>
      </div>
      <template #actions>
        <button class="btn" @click="relabelTarget = null">{{ t.common.cancel }}</button>
        <button
          class="btn btn--mark"
          :disabled="busy || relabelZone === relabelTarget?.zone"
          @click="saveRelabel"
        >
          {{ t.common.save }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.detail {
  padding: var(--s6);
  max-width: 1560px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s5);
}

.detail__head {
  display: flex;
  align-items: flex-end;
  gap: var(--s5);
}

.back {
  display: inline-block;
  margin-bottom: 6px;
  text-decoration: none;
  color: var(--ink-3);
}

.back:hover {
  color: var(--mark);
}

.report {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
}

.why > summary {
  cursor: pointer;
  list-style: none;
}

.why > summary::marker {
  content: '';
}

.reasons {
  margin: var(--s3) 0 0;
  padding-left: 18px;
  display: grid;
  gap: 6px;
  font-size: var(--t-sm);
  color: var(--ink-2);
  max-width: 90ch;
}

.figures {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s2) var(--s4);
  padding-top: var(--s2);
  border-top: 1px solid var(--rule);
}

.figure {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--t-xs);
}

.swatch {
  width: 9px;
  height: 9px;
  border-radius: 2px;
}

.figure__label {
  font-family: var(--mono);
  color: var(--ink-3);
}

.figure__value {
  font-size: var(--t-xs);
}

.workspace {
  display: grid;
  grid-template-columns: minmax(320px, 420px) 1fr;
  gap: var(--s4);
  min-height: 60vh;
}

.panel {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  padding: var(--s4);
  min-height: 0;
}

.panel__controls {
  display: grid;
  gap: var(--s2);
}

.toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--t-sm);
  color: var(--ink-2);
}

.objectives {
  display: grid;
  gap: 4px;
  padding: var(--s3);
  border-radius: var(--r-md);
  background: var(--chrome);
  font-size: var(--t-sm);
}

.objective {
  color: var(--ink-2);
}

.blocks {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 3px;
  max-height: 62vh;
}

.block {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: var(--s2);
  border-left: 3px solid var(--zone);
  border-radius: 0 var(--r-md) var(--r-md) 0;
  padding-right: var(--s2);
  transition: background var(--fast);
}

.block:hover,
.block--on {
  background: var(--chrome);
}

.block--on {
  outline: 1px solid var(--mark);
}

.block__body {
  display: grid;
  gap: 3px;
  padding: var(--s2) var(--s3);
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
  min-width: 0;
}

.block__zone {
  display: flex;
  gap: 6px;
  align-items: center;
}

.flagword {
  font-style: normal;
  color: var(--warn);
}

.block__text {
  font-family: var(--serif);
  font-size: var(--t-sm);
  line-height: 1.45;
  color: var(--ink);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.viewer {
  display: flex;
  flex-direction: column;
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  overflow: hidden;
  min-height: 0;
}

.viewer__bar {
  display: flex;
  align-items: center;
  gap: var(--s3);
  padding: var(--s2) var(--s3);
  border-bottom: 1px solid var(--rule);
  background: var(--chrome);
  font-size: var(--t-sm);
}

.quote {
  padding: var(--s3);
  border-radius: var(--r-md);
  background: var(--chrome);
  font-size: var(--t-sm);
  max-height: 180px;
  overflow: auto;
}

.pad {
  padding: var(--s5);
  text-align: center;
  font-size: var(--t-sm);
}

@media (max-width: 1100px) {
  .workspace {
    grid-template-columns: 1fr;
  }
}
</style>
