<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError } from '@/api/client'
import { sharingApi, type ReviewLinkRow } from '@/api/sharing'
import LoadingState from '@/components/LoadingState.vue'
import ModalDialog from '@/components/ModalDialog.vue'
import ReviewLinkButton from '@/components/ReviewLinkButton.vue'
import { fill, t } from '@/i18n'
import { useRunsStore } from '@/stores/runs'
import { runTitle } from '@/titles'

/**
 * Every shared stand with its reviewer responses, one click from the feedback
 * in the script. Without `run` it lists all of them and shares a chosen run;
 * with `run` it is that run's own feedback and its sharing controls.
 */
const props = defineProps<{ run?: { id: string; ready: boolean } }>()

const READY = new Set(['completed', 'in_review', 'reviewed'])

const runs = useRunsStore()
const links = ref<ReviewLinkRow[] | null>(null)
const error = ref('')
const creating = ref(false)
const chosen = ref('')

const shareable = computed(() => runs.items.filter((run) => READY.has(run.status)))
const pickerError = ref('')
const runsLoaded = ref(false)
let createEpoch = 0

const STATUS_ORDER: ReviewLinkRow['status'][] = ['active', 'expired', 'revoked']

/** Non-empty state groups. "Aktiv" stays first; order inside a group is the list order. */
const groups = computed(() => {
  const rows = links.value ?? []
  return STATUS_ORDER.flatMap((status) => {
    const items = rows.filter((row) => row.status === status)
    return items.length ? [{ status, items }] : []
  })
})

function when(value: string): string {
  return new Date(value).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}

function source(row: ReviewLinkRow) {
  return row.target_kind === 'series'
    ? { name: 'series', params: { id: row.target_id } }
    : { name: 'run', params: { id: row.target_id }, query: { tab: 'feedback' } }
}

async function load(): Promise<void> {
  error.value = ''
  try {
    const index = await sharingApi.all(props.run ? { kind: 'runs', id: props.run.id } : undefined)
    links.value = index.links
  } catch (exc) {
    error.value = exc instanceof ApiError && exc.detail ? exc.detail : t.feedbackBoard.loadError
  }
}

async function startCreate(): Promise<void> {
  const ticket = ++createEpoch
  chosen.value = ''
  pickerError.value = ''
  runsLoaded.value = false
  creating.value = true
  try {
    await runs.load(undefined, 500)
    if (ticket !== createEpoch) return
    runsLoaded.value = true
  } catch (exc) {
    if (ticket !== createEpoch) return
    pickerError.value =
      exc instanceof ApiError && exc.detail ? exc.detail : t.feedbackBoard.runsError
  }
}

function closeCreate(): void {
  creating.value = false
  void load()
}

onMounted(load)
</script>

<template>
  <div class="board">
    <div v-if="!props.run" class="spread">
      <p class="muted lead">{{ t.feedbackBoard.lead }}</p>
      <button class="btn btn--mark" type="button" data-create @click="startCreate">
        {{ t.feedbackBoard.create }}
      </button>
    </div>

    <p v-if="error" class="notice notice--fail" role="alert">{{ error }}</p>
    <LoadingState v-else-if="!links" />
    <p v-else-if="!links.length" class="muted empty" data-empty>
      {{ props.run ? t.feedbackBoard.emptyRun : t.feedbackBoard.empty }}
    </p>
    <div v-else class="groups">
      <section
        v-for="group in groups"
        :key="group.status"
        class="group"
        :data-group="group.status"
      >
        <h3 class="group__title">
          {{ t.sharing.status[group.status] }}
          <span class="group__count num">{{ group.items.length }}</span>
        </h3>
        <div class="frame">
          <table class="links">
            <thead>
              <tr>
                <th>{{ t.feedbackBoard.stand }}</th>
                <th v-if="!props.run">{{ t.feedbackBoard.source }}</th>
                <th>{{ t.feedbackBoard.status }}</th>
                <th class="right">{{ t.feedbackBoard.responses }}</th>
                <th>{{ t.feedbackBoard.lastActivity }}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in group.items" :key="row.id" :data-link="row.id">
                <td>
                  <RouterLink :to="{ name: 'shared-feedback', params: { id: row.id } }" class="link">
                    {{ row.title || t.sharing.sharedStand }}
                  </RouterLink>
                  <span class="meta block">
                    <template v-if="row.episodes.length > 1">
                      {{ fill(t.sharing.episodes, { n: row.episodes.length }) }} ·
                    </template>
                    <template v-else-if="row.episodes[0] && row.episodes[0] !== row.title">
                      {{ row.episodes[0] }} ·
                    </template>
                    {{ when(row.created_at) }}
                  </span>
                </td>
                <td v-if="!props.run">
                  <RouterLink v-if="row.target_title" :to="source(row)" class="link">
                    {{ row.target_title }}
                  </RouterLink>
                  <span v-else class="muted">{{ t.feedbackBoard.gone }}</span>
                  <span v-if="row.target_kind === 'series'" class="badge badge--mark kind">
                    {{ t.feedbackBoard.series }}
                  </span>
                </td>
                <td>
                  <span class="badge" :class="row.status === 'active' ? 'badge--pass' : 'badge--idle'">
                    {{ t.sharing.status[row.status] }}
                  </span>
                </td>
                <td class="right num">{{ row.responses }}</td>
                <td class="meta">
                  {{ row.last_activity ? when(row.last_activity) : t.feedbackBoard.noActivity }}
                </td>
                <td class="right">
                  <RouterLink
                    class="btn btn--sm"
                    :to="{ name: 'shared-feedback', params: { id: row.id } }"
                  >
                    {{ t.sharing.viewInScript }}
                  </RouterLink>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>

    <section v-if="props.run" class="share">
      <h3 class="h-section">{{ t.feedbackBoard.share }}</h3>
      <ReviewLinkButton
        inline
        kind="runs"
        :target-id="props.run.id"
        :ready="props.run.ready"
        @changed="load"
      />
    </section>

    <ModalDialog
      v-else
      :open="creating"
      :title="t.feedbackBoard.createTitle"
      :lead="t.feedbackBoard.createLead"
      wide
      @close="closeCreate"
    >
      <p v-if="pickerError" class="notice notice--fail" role="alert">{{ pickerError }}</p>
      <div class="field">
        <label for="feedback-run">{{ t.feedbackBoard.pickRun }}</label>
        <select id="feedback-run" v-model="chosen" class="select" data-autofocus>
          <option value="" disabled>{{ t.feedbackBoard.pickPlaceholder }}</option>
          <option v-for="run in shareable" :key="run.id" :value="run.id">
            {{ runTitle(run) }} · {{ when(run.created_at) }}
          </option>
        </select>
        <LoadingState v-if="!pickerError && !runsLoaded" class="hint" />
        <p v-else-if="!pickerError && !shareable.length" class="muted hint">
          {{ t.feedbackBoard.noRuns }}
        </p>
      </div>
      <ReviewLinkButton
        v-if="chosen"
        :key="chosen"
        inline
        kind="runs"
        :target-id="chosen"
        ready
        @changed="load"
      />
    </ModalDialog>
  </div>
</template>

<style scoped>
.board {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
  min-width: 0;
}

.spread {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s4);
  flex-wrap: wrap;
}

.lead {
  max-width: 60ch;
  margin: 0;
}

.empty {
  padding: var(--s5) 0;
}

.groups {
  display: grid;
  gap: var(--s5);
}

.group__title {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--s2);
  margin: 0 0 var(--s2);
  font-size: var(--t-md);
  font-weight: 600;
  letter-spacing: -0.01em;
}

.group__count {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.frame {
  min-width: 0;
  overflow-x: auto;
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
}

.links {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--t-sm);
}

.links th {
  text-align: left;
  font-family: var(--mono);
  font-size: 0.625rem;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-3);
  font-weight: 500;
  padding: var(--s3) var(--s4);
  background: var(--chrome);
  border-bottom: 1px solid var(--rule);
  white-space: nowrap;
}

.links td {
  padding: var(--s3) var(--s4);
  border-bottom: 1px solid var(--rule);
  vertical-align: middle;
}

.links tbody tr:last-child td {
  border-bottom: 0;
}

.links tbody tr:hover {
  background: var(--chrome);
}

.right {
  text-align: right;
}

.block {
  display: block;
  margin-top: 2px;
}

.link {
  color: var(--ink);
  font-weight: 550;
  text-decoration: none;
}

.link:hover {
  color: var(--mark);
}

.btn {
  text-decoration: none;
  white-space: nowrap;
}

.notice {
  padding: var(--s4);
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

.kind {
  margin-left: var(--s2);
}

.share {
  margin-top: var(--s4);
  padding-top: var(--s4);
  border-top: 1px solid var(--rule);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  margin-bottom: var(--s4);
}

.hint {
  font-size: var(--t-sm);
  margin: 0;
}
</style>
