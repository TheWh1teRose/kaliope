<script setup lang="ts">
/**
 * The Sammlung: every collected output of every experiment in one list, with
 * the Dokumente folder tree on the left.
 *
 * It is a tab of the Experimente page. Folders only file; they change no
 * output. A card is filed by dragging it onto a folder, by "In Ordner …", or
 * many at once from the selection bar. Each output reads as it does on its own
 * experiment page (see `experiments/presenters.ts`).
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import type {
  ExperimentOutput,
  ExperimentSummary,
  OutputFolderOut,
  OutputStatus,
} from '@/api/types'
import ArtifactView from '@/components/ArtifactView.vue'
import FolderTree from '@/components/FolderTree.vue'
import ModalDialog from '@/components/ModalDialog.vue'
import MoveToFolderDialog from '@/components/experiments/MoveToFolderDialog.vue'
import OutputCard, { type OutputView } from '@/components/experiments/OutputCard.vue'
import OutputDecision from '@/components/experiments/OutputDecision.vue'
import OutputText from '@/components/experiments/OutputText.vue'
import { experimentViews } from '@/experiments'
import { deleteOutput, listAllOutputs, moveOutputs } from '@/experiments/api'
import { outputFacts, outputWarnings } from '@/experiments/outputMeta'
import { presenterFor } from '@/experiments/presenters'
import { readRevealed } from '@/experiments/verbalized_sampling/vs'
import { t } from '@/i18n'
import { ROOT, useOutputFoldersStore } from '@/stores/folders'

const props = defineProps<{ experiments: ExperimentSummary[] }>()

const DRAG_TYPE = 'text/kalliope-output'
const COMPARE_MAX = 4
const PAGE = 50

const labels = t.collection
const folders = useOutputFoldersStore()
const route = useRoute()
const router = useRouter()

type Output = ExperimentOutput<unknown>

function queryValue(key: string): string {
  const raw = route.query[key]
  const value = Array.isArray(raw) ? raw[0] : raw
  return typeof value === 'string' ? value : ''
}

/** `null` shows everything, `'root'` the unfiled outputs, else a folder id. */
const selected = ref<string | null>(queryValue('folder') || null)
const experiment = ref(queryValue('experiment'))
const search = ref('')
const sort = ref<'new' | 'old'>('new')
const status = ref<OutputStatus | 'none' | ''>('')
const includeSub = ref(true)
const view = ref<OutputView>('text')

const items = ref<Output[]>([])
const total = ref(0)
const allCount = ref(0)
const rootCount = ref(0)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const checked = ref<Set<string>>(new Set())
const dragging = ref(false)
const moving = ref<string[] | null>(null)
const revealed = readRevealed()

type Prompt =
  | { kind: 'create'; parentId: string | null }
  | { kind: 'rename'; folder: OutputFolderOut }
  | { kind: 'move'; folder: OutputFolderOut }
  | { kind: 'remove'; folder: OutputFolderOut }
const prompt = ref<Prompt | null>(null)
const draftName = ref('')
const draftParent = ref<string | null>(null)
const promptError = ref('')
const busy = ref(false)

const titles = computed(() => new Map(props.experiments.map((e) => [e.key, e.title])))

const breadcrumb = computed(() =>
  selected.value && selected.value !== ROOT ? folders.pathOf(selected.value) : [],
)

/** Folders a moving folder may go into: everything except itself and its subtree. */
const destinations = computed(() => {
  const subject = prompt.value?.kind === 'move' ? prompt.value.folder : null
  if (!subject) return folders.items
  const blocked = new Set([subject.id, ...folders.descendants(subject.id)])
  return folders.items.filter((folder) => !blocked.has(folder.id))
})

const promptTitle = computed(() => {
  switch (prompt.value?.kind) {
    case 'create':
      return t.folders.newTitle
    case 'rename':
      return t.folders.renameTitle
    case 'move':
      return t.folders.moveTitle
    case 'remove':
      return t.folders.deleteTitle
    default:
      return ''
  }
})

const movingCurrent = computed(() => {
  const ids = moving.value
  if (!ids || ids.length !== 1) return undefined
  return items.value.find((item) => item.id === ids[0])?.folder_id ?? null
})

function fail(exc: unknown): void {
  error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
}

async function load(append = false): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const page = await listAllOutputs({
      folderId: selected.value,
      includeSub: includeSub.value,
      experiment: experiment.value || undefined,
      q: search.value.trim() || undefined,
      status: status.value || undefined,
      sort: sort.value,
      offset: append ? items.value.length : 0,
      limit: PAGE,
    })
    items.value = append ? [...items.value, ...page.items] : page.items
    total.value = page.total
    allCount.value = page.all_count
    rootCount.value = page.root_count
  } catch (exc) {
    fail(exc)
  } finally {
    loading.value = false
  }
}

async function refresh(): Promise<void> {
  await Promise.all([load(), folders.load().catch(fail)])
}

let timer: ReturnType<typeof setTimeout> | undefined
watch(search, () => {
  clearTimeout(timer)
  timer = setTimeout(() => void load(), 250)
})
watch([selected, experiment, sort, includeSub, status], () => {
  checked.value = new Set()
  void load()
  const query = { ...route.query }
  if (selected.value) query.folder = selected.value
  else delete query.folder
  if (experiment.value) query.experiment = experiment.value
  else delete query.experiment
  void router.replace({ query })
})

onMounted(refresh)

// ------------------------------------------------------------- presentation

function present(output: Output) {
  return presenterFor(output.experiment_key, revealed)
}

function experimentTitle(output: Output): string {
  return titles.value.get(output.experiment_key) ?? labels.unknownExperiment
}

function when(value: string): string {
  return new Date(value).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}

function facts(output: Output): string[] {
  const out = outputFacts(output.meta)
  if (output.created_by) out.push(`${labels.by} ${output.created_by}`)
  return out
}

// ---------------------------------------------------------------- selection

function toggle(id: string, on: boolean): void {
  const next = new Set(checked.value)
  if (on) next.add(id)
  else next.delete(id)
  checked.value = next
}

function replace(updated: Output): void {
  items.value = items.value.map((item) => (item.id === updated.id ? updated : item))
}

function compare(): void {
  void router.push({ name: 'experiment-compare', query: { ids: [...checked.value].join(',') } })
}

function startDrag(event: DragEvent, output: Output): void {
  const ids = checked.value.has(output.id) ? [...checked.value] : [output.id]
  event.dataTransfer?.setData(DRAG_TYPE, ids.join(','))
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
  dragging.value = true
}

async function file(ids: string[], folderId: string | null): Promise<void> {
  moving.value = null
  error.value = ''
  try {
    await moveOutputs(ids, folderId)
    const where = folderId ? folders.pathOf(folderId).map((f) => f.name).join(' / ') : labels.root
    notice.value = `${ids.length} ${labels.outputs} ${labels.movedTo} ${where}`
    checked.value = new Set()
    await refresh()
  } catch (exc) {
    fail(exc)
  }
}

async function remove(output: Output): Promise<void> {
  if (!window.confirm(t.experiments.deleteConfirm)) return
  try {
    await deleteOutput(output.id)
    toggle(output.id, false)
    await refresh()
  } catch (exc) {
    fail(exc)
  }
}

// ------------------------------------------------------------------ folders

function openPrompt(next: Prompt): void {
  prompt.value = next
  promptError.value = ''
  draftName.value = next.kind === 'rename' ? next.folder.name : ''
  draftParent.value =
    next.kind === 'create' ? next.parentId : next.kind === 'move' ? next.folder.parent_id : null
}

async function confirmPrompt(): Promise<void> {
  const current = prompt.value
  if (!current) return
  busy.value = true
  promptError.value = ''
  try {
    if (current.kind === 'create') await folders.create(draftName.value, draftParent.value)
    if (current.kind === 'rename') await folders.rename(current.folder.id, draftName.value)
    if (current.kind === 'move') await folders.move(current.folder.id, draftParent.value)
    if (current.kind === 'remove') {
      const gone = new Set([current.folder.id, ...folders.descendants(current.folder.id)])
      await folders.remove(current.folder.id, true)
      if (selected.value && gone.has(selected.value)) selected.value = null
      await load()
    }
    prompt.value = null
  } catch (exc) {
    promptError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="lib">
    <FolderTree
      :folders="folders.items"
      :selected="selected"
      :dropping="dragging"
      :total-count="allCount"
      :root-count="rootCount"
      :count-of="(folder) => folder.total_output_count"
      :drag-type="DRAG_TYPE"
      :labels="{ title: labels.folders, all: labels.all, root: labels.root }"
      @select="selected = $event"
      @drop-items="file($event.ids, $event.folderId)"
      @create="openPrompt({ kind: 'create', parentId: $event })"
      @rename="openPrompt({ kind: 'rename', folder: $event })"
      @move="openPrompt({ kind: 'move', folder: $event })"
      @remove="openPrompt({ kind: 'remove', folder: $event })"
    />

    <div class="pane">
      <form class="tools" role="search" @submit.prevent>
        <div class="field field--q">
          <label for="sammlung-q">{{ labels.search }}</label>
          <input
            id="sammlung-q"
            v-model="search"
            class="input"
            type="search"
            :placeholder="labels.searchPlaceholder"
          />
        </div>
        <div class="field">
          <label for="sammlung-exp">{{ labels.experiment }}</label>
          <select id="sammlung-exp" v-model="experiment" class="select">
            <option value="">{{ labels.allExperiments }}</option>
            <option v-for="item in experiments" :key="item.key" :value="item.key">
              {{ item.title }}
            </option>
          </select>
        </div>
        <div class="field">
          <label for="sammlung-status">{{ labels.status }}</label>
          <select id="sammlung-status" v-model="status" class="select">
            <option value="">{{ labels.allStatuses }}</option>
            <option v-for="(name, key) in labels.statuses" :key="key" :value="key">
              {{ name }}
            </option>
            <option value="none">{{ labels.noStatus }}</option>
          </select>
        </div>
        <div class="field">
          <label for="sammlung-sort">{{ labels.sort }}</label>
          <select id="sammlung-sort" v-model="sort" class="select">
            <option value="new">{{ labels.newest }}</option>
            <option value="old">{{ labels.oldest }}</option>
          </select>
        </div>
      </form>

      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p class="sr-only" role="status" aria-live="polite">{{ notice }}</p>

      <div v-if="checked.size" class="bulk" role="region" :aria-label="labels.selected">
        <span class="num">{{ checked.size }} {{ labels.selected }}</span>
        <span class="grow" />
        <button
          class="btn btn--sm btn--mark"
          :disabled="checked.size < 2 || checked.size > COMPARE_MAX"
          @click="compare"
        >
          {{ labels.compare }}
          {{ checked.size > COMPARE_MAX ? `(${labels.compareMax})` : `(${checked.size})` }}
        </button>
        <button class="btn btn--sm" @click="moving = [...checked]">{{ labels.moveTo }}</button>
        <button class="btn btn--sm" @click="checked = new Set()">
          {{ labels.clearSelection }}
        </button>
      </div>

      <div class="listhead">
        <div class="grow">
          <p class="crumbs meta">
            <template v-if="breadcrumb.length">
              <button class="crumb" @click="selected = null">{{ labels.all }}</button>
              <template v-for="step in breadcrumb" :key="step.id">
                <span aria-hidden="true"> / </span>
                <button class="crumb" @click="selected = step.id">{{ step.name }}</button>
              </template>
            </template>
            <template v-else>{{ selected === 'root' ? labels.root : labels.all }}</template>
          </p>
          <p class="eyebrow">
            {{ items.length }} {{ labels.of }} {{ total }} {{ labels.outputs }}
          </p>
        </div>
        <label v-if="selected && selected !== 'root'" class="meta check">
          <input v-model="includeSub" type="checkbox" /> {{ labels.includeSub }}
        </label>
        <span class="meta">{{ t.experiments.showAs }}</span>
        <span class="seg" role="group">
          <button :aria-pressed="view === 'text'" @click="view = 'text'">
            {{ t.experiments.text }}
          </button>
          <button :aria-pressed="view === 'json'" @click="view = 'json'">
            {{ t.experiments.json }}
          </button>
        </span>
      </div>

      <p v-if="!items.length && !loading" class="empty">
        {{ allCount ? labels.empty : labels.emptyAll }}
      </p>

      <div class="cards">
        <div
          v-for="output in items"
          :key="output.id"
          class="slot"
          :class="{ 'slot--on': checked.has(output.id) }"
          draggable="true"
          @dragstart="startDrag($event, output)"
          @dragend="dragging = false"
        >
          <OutputCard
            :title="present(output).titleOf(output)"
            :payload="present(output).payloadOf(output)"
            :text="output.text"
            :badges="[{ text: experimentTitle(output) }, ...present(output).badgesOf(output)]"
            :facts="facts(output)"
            :warnings="outputWarnings(output.meta)"
            :view="view"
            collapsible
          >
            <template #lead>
              <input
                class="check__box"
                type="checkbox"
                :checked="checked.has(output.id)"
                :aria-label="`${present(output).titleOf(output)} ${labels.select}`"
                @change="toggle(output.id, ($event.target as HTMLInputElement).checked)"
              />
            </template>
            <template #head>
              <button class="folderchip" :title="labels.moveTitle" @click="moving = [output.id]">
                {{
                  output.folder_path.length ? `▸ ${output.folder_path.join(' / ')}` : labels.root
                }}
              </button>
              <span class="meta">{{ when(output.created_at) }}</span>
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
              <button class="btn btn--sm" @click="moving = [output.id]">
                {{ labels.moveTo }}
              </button>
              <RouterLink
                v-if="experimentViews[output.experiment_key]"
                class="btn btn--sm btn--ghost"
                :to="{ name: 'experiment', params: { key: output.experiment_key } }"
              >
                {{ labels.openExperiment }}
              </RouterLink>
              <span class="grow" />
              <button class="btn btn--ghost btn--sm btn--danger" @click="remove(output)">
                {{ t.experiments.delete }}
              </button>
            </template>
          </OutputCard>
        </div>
      </div>

      <button
        v-if="items.length < total"
        class="btn more"
        :disabled="loading"
        @click="load(true)"
      >
        {{ labels.loadMore }}
      </button>
    </div>

    <MoveToFolderDialog
      :open="Boolean(moving)"
      :count="moving?.length ?? 0"
      :current="movingCurrent"
      @close="moving = null"
      @move="file(moving ?? [], $event)"
    />

    <ModalDialog
      :open="Boolean(prompt)"
      :title="promptTitle"
      :lead="prompt?.kind === 'remove' ? labels.deleteLead : ''"
      @close="prompt = null"
    >
      <div class="stack">
        <p v-if="prompt?.kind === 'remove'" class="quote">{{ prompt.folder.name }}</p>
        <div v-if="prompt?.kind === 'create' || prompt?.kind === 'rename'" class="field">
          <label for="output-folder-name">{{ t.folders.name }}</label>
          <input id="output-folder-name" v-model="draftName" class="input" data-autofocus />
        </div>
        <p v-if="promptError" class="error" role="alert">{{ promptError }}</p>
        <div v-if="prompt?.kind === 'create' || prompt?.kind === 'move'" class="field">
          <label for="output-folder-parent">{{ t.folders.parent }}</label>
          <select id="output-folder-parent" v-model="draftParent" class="select">
            <option :value="null">{{ labels.root }}</option>
            <option v-for="folder in destinations" :key="folder.id" :value="folder.id">
              {{ folder.path.join(' / ') }}
            </option>
          </select>
        </div>
      </div>
      <template #actions>
        <button class="btn" @click="prompt = null">{{ t.common.cancel }}</button>
        <button
          class="btn"
          :class="prompt?.kind === 'remove' ? 'btn--danger' : 'btn--primary'"
          :disabled="busy"
          @click="confirmPrompt"
        >
          {{ prompt?.kind === 'remove' ? t.common.delete : t.common.save }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.lib {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: var(--s5);
  align-items: start;
  padding: var(--s4) var(--s6) var(--s7);
}

.pane {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  min-width: 0;
}

.tools {
  display: flex;
  flex-wrap: wrap;
  gap: var(--s2);
  align-items: flex-end;
  padding: var(--s3);
  background: var(--chrome);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
}

.tools .field {
  flex: 1 1 140px;
  max-width: 220px;
}

.tools .field--q {
  flex: 3 1 240px;
  max-width: none;
}

.tools .input,
.tools .select {
  padding: 6px 8px;
}

.bulk {
  position: sticky;
  top: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  padding: var(--s2) var(--s3);
  border-radius: var(--r-md);
  background: var(--ink);
  color: var(--chrome);
  box-shadow: var(--shadow-md);
}

.bulk .btn {
  background: transparent;
  color: var(--chrome);
  border-color: var(--ink-3);
}

.bulk .btn--mark {
  background: var(--mark);
  border-color: var(--mark);
  color: #fff;
}

.bulk .btn:hover:not(:disabled) {
  background: var(--ink-2);
  border-color: var(--ink-2);
  color: var(--chrome);
}

.listhead {
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex-wrap: wrap;
}

.crumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
}

.crumb {
  border: 0;
  background: transparent;
  padding: 0;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.crumb:hover {
  color: var(--mark);
}

.check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.check input,
.check__box {
  accent-color: var(--mark);
  margin: 0;
}

.check__box {
  width: 16px;
  height: 16px;
  flex: none;
  cursor: pointer;
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

.cards {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

.slot {
  border-radius: var(--r-lg);
  cursor: grab;
}

.slot--on :deep(.out) {
  border-color: var(--mark);
  box-shadow:
    0 0 0 3px var(--mark-soft),
    var(--shadow-sm);
}

a.btn {
  text-decoration: none;
}

.folderchip {
  display: inline-flex;
  align-items: center;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 1px 7px;
  border: 1px dashed var(--rule-strong);
  border-radius: 999px;
  background: transparent;
  color: var(--ink-2);
  font-family: var(--mono);
  font-size: var(--t-xs);
  cursor: pointer;
}

.folderchip:hover {
  border-color: var(--mark);
  color: var(--mark-deep);
}

.empty {
  padding: var(--s6);
  text-align: center;
  border: 1px dashed var(--rule-strong);
  border-radius: var(--r-lg);
  color: var(--ink-3);
  font-size: var(--t-sm);
}

.error {
  padding: var(--s3) var(--s4);
  background: var(--fail-soft);
  color: var(--fail);
  border-radius: var(--r-md);
}

.more {
  align-self: center;
}

.stack {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
}

@media (max-width: 920px) {
  .lib {
    grid-template-columns: minmax(0, 1fr);
    padding: var(--s3) var(--s4) var(--s6);
  }

  .tools .field {
    flex-basis: 100%;
    max-width: none;
  }
}
</style>
