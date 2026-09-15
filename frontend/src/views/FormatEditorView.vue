<script setup lang="ts">
/**
 * The format specification editor (§6.5).
 *
 * A format is data, not prompt text: the outline and script nodes read the
 * speakers, register, length and guidance off it and build their own prompts.
 * Editing it therefore changes how every future run of every pipeline sounds,
 * which is why it is versioned exactly like a pipeline.
 *
 * A run keeps its own copy of the format it started with, so editing one here
 * never rewrites what an existing run produced.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, onBeforeRouteLeave } from 'vue-router'

import { ApiError } from '@/api/client'
import type { FormatDetail, FormatSpec, RevisionOut, SpeakerSpec } from '@/api/types'
import ModalDialog from '@/components/ModalDialog.vue'
import RevisionList from '@/components/RevisionList.vue'
import { t } from '@/i18n'
import { usePipelinesStore } from '@/stores/pipelines'

const props = defineProps<{ id: string }>()

const store = usePipelinesStore()

const detail = ref<FormatDetail | null>(null)
const spec = ref<FormatSpec | null>(null)
const saved = ref('')
const revisions = ref<RevisionOut[]>([])

const loading = ref(true)
const busy = ref(false)
const error = ref('')
const saving = ref(false)
const note = ref('')
const restoreTarget = ref<number | null>(null)

const dirty = computed(() => JSON.stringify(spec.value) !== saved.value)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    apply(await store.getFormat(props.id))
    revisions.value = await store.formatVersions(props.id)
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    loading.value = false
  }
}

function apply(loaded: FormatDetail): void {
  detail.value = loaded
  spec.value = JSON.parse(JSON.stringify(loaded.spec)) as FormatSpec
  saved.value = JSON.stringify(spec.value)
}

function addSpeaker(): void {
  if (!spec.value) return
  const next: SpeakerSpec = {
    id: `speaker${spec.value.speakers.length + 1}`,
    name: '',
    role: '',
    voice_note: null,
  }
  spec.value.speakers.push(next)
}

function removeSpeaker(index: number): void {
  spec.value?.speakers.splice(index, 1)
}

async function save(): Promise<void> {
  if (!spec.value) return
  busy.value = true
  error.value = ''
  try {
    apply(await store.saveFormat(props.id, spec.value, note.value.trim() || null))
    revisions.value = await store.formatVersions(props.id)
    saving.value = false
    note.value = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

async function restore(): Promise<void> {
  const revision = restoreTarget.value
  if (revision === null) return
  busy.value = true
  try {
    apply(await store.restoreFormat(props.id, revision))
    revisions.value = await store.formatVersions(props.id)
    restoreTarget.value = null
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}

function discard(): void {
  if (detail.value) apply(detail.value)
}

onBeforeRouteLeave(() => (dirty.value ? window.confirm(t.pipelines.leaveConfirm) : true))

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="head">
      <div class="grow">
        <RouterLink :to="{ name: 'pipelines' }" class="eyebrow back">
          ← {{ t.formats.title }}
        </RouterLink>
        <h1 class="h-page">{{ spec?.name || props.id }}</h1>
        <p class="meta">
          <code>{{ props.id }}</code>
          · {{ t.pipelines.revision }} {{ detail?.revision ?? 0 }}
          ·
          {{ detail?.origin === 'file' ? t.pipelines.originFile : t.pipelines.originUser }}
          <template v-if="detail?.run_count">
            · {{ detail.run_count }} {{ t.pipelines.runs }}
          </template>
        </p>
      </div>
      <div class="row wrap">
        <span v-if="dirty" class="badge badge--warn">{{ t.common.unsaved }}</span>
        <button v-if="dirty" class="btn btn--sm" @click="discard">{{ t.common.discard }}</button>
        <button class="btn btn--primary" :disabled="!dirty || busy" @click="saving = true">
          {{ t.pipelines.save }}
        </button>
      </div>
    </header>

    <p v-if="error" class="banner banner--fail">{{ error }}</p>
    <p v-if="loading || !spec" class="muted pad">{{ t.common.loading }}</p>

    <div v-else class="layout">
      <div class="main">
        <section class="card block">
          <div class="grid3">
            <div class="field">
              <label for="f-name">{{ t.formats.name }}</label>
              <input id="f-name" v-model="spec.name" class="input" />
            </div>
            <div class="field">
              <label for="f-register">{{ t.formats.register }}</label>
              <input id="f-register" v-model="spec.register" class="input" />
              <p class="hint">{{ t.formats.registerHint }}</p>
            </div>
            <div class="field">
              <label for="f-minutes">{{ t.formats.targetMinutes }}</label>
              <input
                id="f-minutes"
                v-model.number="spec.target_minutes"
                class="input"
                type="number"
                min="1"
              />
              <p class="hint">{{ t.formats.targetMinutesHint }}</p>
            </div>
          </div>
        </section>

        <section class="card block">
          <div class="spread">
            <p class="eyebrow">{{ t.formats.speakers }}</p>
            <button class="btn btn--sm" @click="addSpeaker">+ {{ t.formats.addSpeaker }}</button>
          </div>
          <p class="hint">{{ t.formats.speakersHint }}</p>
          <p v-if="!spec.speakers.length" class="issue issue--fail">{{ t.formats.needSpeaker }}</p>

          <div v-for="(speaker, index) in spec.speakers" :key="index" class="speaker">
            <div class="speaker__grid">
              <div class="field">
                <label :for="`sp-id-${index}`">{{ t.formats.speakerId }}</label>
                <input :id="`sp-id-${index}`" v-model="speaker.id" class="input mono" />
              </div>
              <div class="field">
                <label :for="`sp-name-${index}`">{{ t.formats.speakerName }}</label>
                <input :id="`sp-name-${index}`" v-model="speaker.name" class="input" />
              </div>
              <div class="field grow">
                <label :for="`sp-role-${index}`">{{ t.formats.speakerRole }}</label>
                <input :id="`sp-role-${index}`" v-model="speaker.role" class="input" />
              </div>
              <button
                class="btn btn--ghost btn--sm btn--danger"
                :title="t.formats.removeSpeaker"
                @click="removeSpeaker(index)"
              >
                ✕
              </button>
            </div>
            <div class="field">
              <label :for="`sp-voice-${index}`">{{ t.formats.speakerVoice }}</label>
              <textarea
                :id="`sp-voice-${index}`"
                v-model="speaker.voice_note"
                class="textarea short"
                rows="2"
              />
              <p class="hint">{{ t.formats.speakerVoiceHint }}</p>
            </div>
          </div>
        </section>

        <section class="card block">
          <div class="field">
            <label for="f-opening">{{ t.formats.opening }}</label>
            <textarea id="f-opening" v-model="spec.opening" class="textarea short" rows="2" />
            <p class="hint">{{ t.formats.openingHint }}</p>
          </div>
          <div class="field">
            <label for="f-closing">{{ t.formats.closing }}</label>
            <textarea id="f-closing" v-model="spec.closing" class="textarea short" rows="2" />
            <p class="hint">{{ t.formats.closingHint }}</p>
          </div>
          <div class="field">
            <label for="f-beats">{{ t.formats.beatsHint }}</label>
            <textarea id="f-beats" v-model="spec.beats_hint" class="textarea short" rows="3" />
            <p class="hint">{{ t.formats.beatsHintHint }}</p>
          </div>
        </section>
      </div>

      <aside class="side card">
        <p class="eyebrow">{{ t.pipelines.history }}</p>
        <p class="hint">{{ t.pipelines.historyLead }}</p>
        <RevisionList
          :revisions="revisions"
          :current="detail?.revision ?? 0"
          :busy="busy"
          @restore="(revision) => (restoreTarget = revision)"
        />
      </aside>
    </div>

    <ModalDialog
      :open="saving"
      :title="t.formats.saveTitle"
      :lead="t.pipelines.saveLead"
      @close="saving = false"
    >
      <div class="field">
        <label for="f-note">{{ t.pipelines.note }}</label>
        <textarea id="f-note" v-model="note" class="textarea" rows="3" data-autofocus />
      </div>
      <template #actions>
        <button class="btn" @click="saving = false">{{ t.common.cancel }}</button>
        <button class="btn btn--primary" :disabled="busy" @click="save">
          {{ busy ? t.common.saving : t.common.save }}
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="restoreTarget !== null"
      :title="t.pipelines.restoreTitle"
      :lead="t.pipelines.restoreLead"
      @close="restoreTarget = null"
    >
      <p class="prose num">r{{ restoreTarget }}</p>
      <template #actions>
        <button class="btn" @click="restoreTarget = null">{{ t.common.cancel }}</button>
        <button class="btn btn--primary" :disabled="busy" @click="restore">
          {{ t.pipelines.restore }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.page {
  padding: var(--s6);
  max-width: 1320px;
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

.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: var(--s4);
  align-items: start;
}

.main {
  display: grid;
  gap: var(--s3);
  min-width: 0;
}

.block {
  padding: var(--s4);
  display: grid;
  gap: var(--s3);
}

.grid3 {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr;
  gap: var(--s4);
}

.speaker {
  display: grid;
  gap: var(--s3);
  padding: var(--s3);
  border-radius: var(--r-md);
  background: var(--chrome);
  border: 1px solid var(--rule);
}

.speaker__grid {
  display: grid;
  grid-template-columns: 120px 1fr 2fr auto;
  gap: var(--s3);
  align-items: end;
}

.mono {
  font-family: var(--mono);
  font-size: var(--t-sm);
}

.short {
  font-family: var(--sans);
  font-size: var(--t-sm);
  min-height: 0;
}

.hint {
  margin: 0;
  font-size: var(--t-xs);
  color: var(--ink-3);
  line-height: 1.5;
  max-width: 82ch;
}

.issue {
  padding: var(--s2) var(--s3);
  border-radius: var(--r-sm);
  font-size: var(--t-sm);
  margin: 0;
}

.issue--fail {
  background: var(--fail-soft);
  color: var(--fail);
}

.banner {
  margin: 0;
  padding: var(--s3) var(--s4);
  border-radius: var(--r-md);
  font-size: var(--t-sm);
}

.banner--fail {
  background: var(--fail-soft);
  color: var(--fail);
}

.side {
  position: sticky;
  top: var(--s4);
  padding: var(--s4);
  display: grid;
  gap: var(--s2);
  max-height: calc(100vh - var(--s7));
  overflow: auto;
}

.pad {
  padding: var(--s7);
  text-align: center;
}

@media (max-width: 1080px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .side {
    position: static;
  }
  .grid3,
  .speaker__grid {
    grid-template-columns: 1fr;
  }
}
</style>
