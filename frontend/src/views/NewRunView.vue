<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import type { AudienceSpec, DocumentSummary } from '@/api/types'
import { t } from '@/i18n'
import { useCatalogueStore } from '@/stores/catalogue'
import { useDocumentsStore } from '@/stores/documents'
import { useRunsStore } from '@/stores/runs'

const props = defineProps<{ id: string }>()

const documents = useDocumentsStore()
const catalogue = useCatalogueStore()
const runs = useRunsStore()
const router = useRouter()

const document = ref<DocumentSummary | null>(null)
const flowId = ref('baseline_v0')
const formatId = ref('two_host_dialogue')
const targetMinutes = ref(15)
const audience = ref<AudienceSpec>({
  description: '',
  prior_knowledge: '',
  listening_context: '',
  desired_outcome: '',
})
const busy = ref(false)
const error = ref('')

async function submit(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    const run = await runs.create({
      document_id: props.id,
      flow_id: flowId.value,
      format_id: formatId.value,
      target_minutes: targetMinutes.value,
      audience_spec: audience.value.description.trim() ? audience.value : undefined,
    })
    await router.push({ name: 'run', params: { id: run.id } })
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : t.errors.generic
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  document.value = await documents.get(props.id)
  const format = catalogue.formats.find((f) => f.id === formatId.value)
  if (format) targetMinutes.value = format.target_minutes
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
      <div class="pair">
        <div class="field">
          <label for="flow">{{ t.run.flow }}</label>
          <select id="flow" v-model="flowId" class="select">
            <option v-for="flow in catalogue.flows" :key="flow.id" :value="flow.id">
              {{ flow.id }} v{{ flow.version }}
            </option>
          </select>
          <p class="hint">
            {{ catalogue.flows.find((f) => f.id === flowId)?.description }}
          </p>
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

      <div class="field minutes">
        <label for="minutes">{{ t.run.targetMinutes }}</label>
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
        <button class="btn btn--primary" type="submit" :disabled="busy">
          {{ busy ? t.run.starting : t.run.start }}
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
  grid-template-columns: 1fr 1fr;
  gap: var(--s4);
}

.hint {
  font-size: var(--t-xs);
  color: var(--ink-3);
  line-height: 1.4;
}

.minutes__out {
  font-size: var(--t-xl);
  font-weight: 600;
  min-width: 2.5ch;
  text-align: right;
  letter-spacing: -0.02em;
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
    grid-template-columns: 1fr;
  }
}
</style>
