<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ApiError } from '@/api/client'
import {
  sharingApi,
  sharedDate,
  sharedDuration,
  type OwnerLinks,
  type ShareOptions,
  type SharePreview,
  type ShareSelection,
} from '@/api/sharing'
import ModalDialog from '@/components/ModalDialog.vue'

import { fill, t } from '@/i18n'

const props = defineProps<{
  kind: 'runs' | 'series'
  targetId: string
  ready: boolean
  inline?: boolean
}>()
const open = ref(false)
const busy = ref(false)
const loading = ref(false)
const error = ref('')
const readinessError = ref('')
const options = ref<ShareOptions | null>(null)
const management = ref<OwnerLinks | null>(null)
const draft = ref<ShareSelection>({
  title: '',
  episodes: [],
  acknowledge_missing_audio: false,
})
const preview = ref<SharePreview | null>(null)
const url = ref('')
const copyState = ref('')
const replaceConfirmation = ref(false)
const revokeId = ref<string | null>(null)
const active = computed(
  () =>
    management.value?.links.find((link) => link.status === 'active') ?? null,
)
const missingAudio = computed(() =>
  draft.value.episodes.some((e) => !e.take_id),
)
const valid = computed(
  () =>
    props.ready &&
    !!options.value &&
    draft.value.title.trim() &&
    draft.value.episodes.every((e) => e.title.trim()) &&
    (!missingAudio.value || draft.value.acknowledge_missing_audio),
)
let generation = 0
function message(exc: unknown): string {
  return exc instanceof ApiError ? exc.detail : t.sharing.actionError
}
watch(
  draft,
  () => {
    preview.value = null
    replaceConfirmation.value = false
  },
  { deep: true },
)
async function show(): Promise<void> {
  const current = ++generation
  open.value = true
  busy.value = false
  loading.value = true
  error.value = ''
  readinessError.value = ''
  url.value = ''
  copyState.value = ''
  preview.value = null
  options.value = null
  management.value = null
  revokeId.value = null
  const results = await Promise.allSettled([
    sharingApi.links(props.kind, props.targetId),
    sharingApi.options(props.kind, props.targetId),
  ])
  if (current !== generation) return
  if (results[0].status === 'fulfilled') management.value = results[0].value
  else error.value = message(results[0].reason)
  if (results[1].status === 'fulfilled') {
    options.value = results[1].value
    draft.value = {
      title: options.value.title,
      acknowledge_missing_audio: false,
      episodes: options.value.episodes.map((e) => ({
        index: e.index,
        title: e.title,
        take_id: e.takes[0]?.id ?? null,
      })),
    }
  } else readinessError.value = message(results[1].reason)
  loading.value = false
}
function close(): void {
  generation++
  open.value = false
  url.value = ''
  preview.value = null
}
onMounted(() => { if (props.inline) void show() })
async function inspect(): Promise<boolean> {
  if (!valid.value || busy.value) return false
  const current = generation
  busy.value = true
  error.value = ''
  try {
    const result = await sharingApi.preview(
      props.kind,
      props.targetId,
      draft.value,
    )
    if (current !== generation) return false
    preview.value = result
    return true
  } catch (exc) {
    if (current === generation) error.value = message(exc)
    return false
  } finally {
    if (current === generation) busy.value = false
  }
}
async function create(confirmed = false): Promise<void> {
  if (!valid.value || busy.value) return
  if (!preview.value && !(await inspect())) return
  if (!preview.value) return
  if (active.value && !confirmed) {
    replaceConfirmation.value = true
    return
  }
  const current = generation
  busy.value = true
  error.value = ''
  try {
    const result = await sharingApi.create(
      props.kind,
      props.targetId,
      draft.value,
      preview.value.key,
      active.value?.id ?? null,
    )
    if (current !== generation) return
    url.value = result.url
    copyState.value = ''
    replaceConfirmation.value = false
    if (management.value)
      management.value.links = [
        result.link,
        ...management.value.links.map((link) => ({
          ...link,
          status: link.status === 'active' ? ('revoked' as const) : link.status,
        })),
      ]
  } catch (exc) {
    if (current === generation) {
      error.value = message(exc)
      if (exc instanceof ApiError && exc.status === 409) {
        preview.value = null
        replaceConfirmation.value = false
      }
    }
  } finally {
    if (current === generation) busy.value = false
  }
}
async function revoke(): Promise<void> {
  if (!revokeId.value || busy.value) return
  const id = revokeId.value
  const current = generation
  busy.value = true
  error.value = ''
  try {
    await sharingApi.revoke(id)
    if (current !== generation) return
    if (management.value)
      management.value.links = management.value.links.map((link) =>
        link.id === id ? { ...link, status: 'revoked' } : link,
      )
    revokeId.value = null
    url.value = ''
    copyState.value = ''
  } catch (exc) {
    if (current === generation) error.value = message(exc)
  } finally {
    if (current === generation) busy.value = false
  }
}
async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(url.value)
    copyState.value = t.sharing.copied
  } catch {
    copyState.value = t.sharing.copyManually
  }
}
const statusLabel = t.sharing.status
</script>

<template>
  <button v-if="!inline" class="btn" type="button" @click="show">{{ t.sharing.link }}</button>
  <component
    :is="inline ? 'section' : ModalDialog"
    :open="open"
    :title="t.sharing.ownerTitle"
    :lead="t.sharing.ownerLead"
    wide
    @close="close"
  >
    <p v-if="loading" role="status">{{ t.sharing.loadingShare }}</p>
    <p v-if="error" class="notice notice--fail" role="alert">{{ error }}</p>
    <button v-if="error && !management" class="btn" @click="show">
      {{ t.common.retry }}
    </button>
    <template v-if="management">
      <p v-if="!management.configured" class="notice">
        {{ t.sharing.notConfigured }}
      </p>
      <p v-if="readinessError" class="notice">{{ readinessError }}</p>
      <form v-if="options" @submit.prevent="create()">
        <div class="field">
          <label for="share-title">{{ t.sharing.publicTitle }}</label
          ><input
            id="share-title"
            v-model="draft.title"
            class="input"
            maxlength="200"
            data-autofocus
            :disabled="busy"
          />
        </div>
        <p class="hint">{{ t.sharing.titleHint }}</p>
        <div
          v-for="(episode, index) in draft.episodes"
          :key="episode.index"
          class="episode-choice"
        >
          <div class="field">
            <label :for="`share-title-${episode.index}`">{{
              fill(t.sharing.episodeTitle, { n: index + 1 })
            }}</label
            ><input
              :id="`share-title-${episode.index}`"
              v-model="episode.title"
              class="input"
              maxlength="200"
              :disabled="busy"
            />
          </div>
          <div class="field">
            <label :for="`share-take-${episode.index}`">{{
              t.sharing.recording
            }}</label
            ><select
              :id="`share-take-${episode.index}`"
              v-model="episode.take_id"
              class="select"
              :disabled="busy"
            >
              <option :value="null">{{ t.sharing.currentScript }}</option>
              <option
                v-for="take in options.episodes[index].takes"
                :key="take.id"
                :value="take.id"
              >
                {{ t.sharing.fullEpisode }} ·
                {{ sharedDate(take.created_at) }} ·
                {{ sharedDuration(take.duration_s)
                }}{{ take.older_script ? t.sharing.olderSuffix : '' }}
              </option>
            </select>
          </div>
          <p
            v-if="
              options.episodes[index].takes.find(
                (t) => t.id === episode.take_id,
              )?.older_script
            "
            class="notice"
          >
            {{ t.sharing.olderWarning }}
          </p>
        </div>
        <label v-if="missingAudio" class="ack"
          ><input
            v-model="draft.acknowledge_missing_audio"
            type="checkbox"
            :disabled="busy"
          /><span>{{ t.sharing.acknowledge }}</span></label
        >
        <p class="notice">{{ t.sharing.validity }}</p>
        <button class="btn btn--primary" :disabled="!valid || busy || !management.configured" type="submit">
          {{ active ? t.sharing.newLink : t.sharing.createLink }}
        </button>
      </form>
      <div
        v-if="replaceConfirmation"
        class="notice"
        role="group"
        :aria-label="t.sharing.replaceLabel"
      >
        <p>{{ t.sharing.replaceWarning }}</p>
        <div class="actions">
          <button
            class="btn"
            :disabled="busy"
            @click="replaceConfirmation = false"
          >
            {{ t.common.cancel }}</button
          ><button
            class="btn btn--primary"
            :disabled="busy"
            @click="create(true)"
          >
            {{ t.sharing.replace }}
          </button>
        </div>
      </div>
      <section v-if="url" class="created">
        <h3 class="h-section">{{ t.sharing.created }}</h3>
        <p class="hint">{{ t.sharing.tokenOnce }}</p>
        <label class="field"
          ><span class="eyebrow">{{ t.sharing.link }}</span
          ><input
            class="input"
            :value="url"
            readonly
            @focus="($event.target as HTMLInputElement).select()"
        /></label>
        <div class="actions">
          <button class="btn" @click="copy">{{ t.sharing.copy }}</button
          >
        </div>
        <p role="status" class="hint">{{ copyState }}</p>
      </section>
      <section v-if="management.links.length" class="management">
        <h3 class="h-section">{{ t.sharing.manage }}</h3>
        <p class="hint">{{ t.sharing.recallLimit }}</p>
        <div v-for="link in management.links" :key="link.id" class="link-row">
          <div>
            <span
              class="badge"
              :class="link.status === 'active' ? 'badge--pass' : 'badge--idle'"
              >{{ statusLabel[link.status] }}</span
            >
            <p class="meta">
              {{
                fill(t.sharing.dates, {
                  created: sharedDate(link.created_at),
                  expires: sharedDate(link.expires_at),
                })
              }}
            </p>
            <div v-if="link.feedback" class="feedback">
              <p class="eyebrow">{{ t.sharing.ownerFeedback }}</p>
              <p v-if="!link.feedback.responses.length" class="meta">
                {{ t.sharing.noFeedback }}
              </p>
              <template v-else>
                <p class="meta">
                  👍 {{ link.feedback.impressed }} · 🤢 {{ link.feedback.dislike }} · 🤮
                  {{ link.feedback.horrible }}
                </p>
                <article
                  v-for="response in link.feedback.responses"
                  :key="response.index"
                  class="response"
                >
                  <p>
                    <b>{{
                      fill(t.sharing.responseLabel, { n: response.index })
                    }}</b>
                  </p>
                  <p v-if="response.stars != null" class="meta">
                    {{
                      fill(t.sharing.starsValue, {
                        n: response.stars.toLocaleString('de-DE'),
                      })
                    }}
                  </p>
                  <p v-if="response.worked" class="meta">
                    {{ t.sharing.worked }} {{ response.worked }}
                  </p>
                  <p v-if="response.did_not" class="meta">
                    {{ t.sharing.didNot }} {{ response.did_not }}
                  </p>
                </article>
                <div v-if="link.feedback.lines.length">
                  <p class="eyebrow">{{ t.sharing.markedLines }}</p>
                  <article
                    v-for="line in link.feedback.lines"
                    :key="`${line.response}-${line.key}`"
                    class="marked"
                  >
                    <p class="meta">
                      {{ line.key }} ·
                      {{
                        line.reaction === 'impressed'
                          ? '👍'
                          : line.reaction === 'dislike'
                            ? '🤢'
                            : '🤮'
                      }}
                      · {{ fill(t.sharing.responseLabel, { n: line.response }) }}
                    </p>
                    <p v-if="line.speaker || line.text">
                      <b>{{ line.speaker }}</b> {{ line.text }}
                    </p>
                    <p v-if="line.comment" class="meta">{{ line.comment }}</p>
                  </article>
                </div>
              </template>
            </div>
          </div>
          <button
            v-if="link.status === 'active'"
            class="btn btn--danger btn--sm"
            :disabled="busy"
            @click="revokeId = link.id"
          >
            {{ t.sharing.revoke }}
          </button>
        </div>
      </section>
      <div
        v-if="revokeId"
        class="notice"
        role="group"
        :aria-label="t.sharing.revokeLink"
      >
        <p>{{ t.sharing.revokeWarning }}</p>
        <div class="actions">
          <button class="btn" :disabled="busy" @click="revokeId = null">
            {{ t.common.cancel }}</button
          ><button class="btn btn--danger" :disabled="busy" @click="revoke">
            {{ t.sharing.revokeLink }}
          </button>
        </div>
      </div>
    </template>
  </component>
</template>

<style scoped>
.field {
  margin-bottom: var(--s3);
}
.hint {
  color: var(--ink-2);
  font-size: var(--t-sm);
  margin: var(--s3) 0;
}
.episode-choice {
  margin: var(--s5) 0;
  padding-top: var(--s4);
  border-top: 1px solid var(--rule);
}
.notice {
  padding: var(--s4);
  margin: var(--s4) 0;
  background: var(--chrome);
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  font-size: var(--t-sm);
}
.notice--fail {
  background: var(--fail-soft);
  color: var(--fail);
  border-color: var(--fail);
}
.ack {
  display: flex;
  gap: var(--s3);
  align-items: flex-start;
  font-size: var(--t-sm);
}
.ack input {
  margin-top: 4px;
}
.actions {
  display: flex;
  gap: var(--s3);
  flex-wrap: wrap;
  margin-top: var(--s4);
}
.preview,
.created,
.management {
  margin-top: var(--s5);
  border-top: 1px solid var(--rule);
  padding-top: var(--s5);
}
.preview h3 {
  margin-bottom: var(--s4);
}
.link-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s3);
  padding: var(--s3) 0;
  border-bottom: 1px solid var(--rule);
  flex-wrap: wrap;
}
.link-row .meta {
  overflow-wrap: anywhere;
  margin-top: var(--s2);
}
.feedback,
.response,
.marked {
  margin-top: var(--s3);
}
.response p,
.marked p {
  margin: 0 0 var(--s2);
  overflow-wrap: anywhere;
}
</style>
