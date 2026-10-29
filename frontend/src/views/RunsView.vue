<script setup lang="ts">
/**
 * The runs overview. Deleting a run only moves it to the trash, which is the
 * second tab: nothing of the run is removed, and its share links keep working,
 * so restoring it is one click. A series goes to the trash with all its
 * episodes, from its own row or from any episode's, and comes back whole.
 * The active tab is the `tab` query.
 *
 * Runs are filed into folders as documents are: the sidebar filters and takes
 * drops, and each row can be renamed or filed. A series is filed as one unit,
 * so moving any of its episodes moves the series. The open folder is the
 * `folder` query, so going into a run and back keeps it.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import type { RunFolderOut, RunOut, SeriesOut } from '@/api/types'
import FolderTree from '@/components/FolderTree.vue'
import LoadingState from '@/components/LoadingState.vue'
import ModalDialog from '@/components/ModalDialog.vue'
import RenameDialog from '@/components/RenameDialog.vue'
import StatusPill from '@/components/StatusPill.vue'
import { fill, t } from '@/i18n'
import { runTitle, seriesTitle } from '@/titles'
import { ROOT, runFolderSummary, useRunFoldersStore } from '@/stores/folders'
import { useRunsStore } from '@/stores/runs'
import { useSeriesStore } from '@/stores/series'

/** How often the list asks again while a run or series is still moving. */
const POLL_MS = 4000
const LIVE_RUN = new Set(['queued', 'running', 'outlined'])
const STOPPABLE_RUN = new Set(['queued', 'running'])
const LIVE_SERIES = new Set(['queued', 'planning', 'outlining', 'writing'])
/** A dragged row carries `run:<id>` or `series:<id>`. */
const DRAG_TYPE = 'text/kalliope-run'

const runs = useRunsStore()
const seriesStore = useSeriesStore()
const route = useRoute()
const router = useRouter()
const series = ref<SeriesOut[]>([])
/** False until a load succeeds. A failure keeps the error up, and later polls refresh silently. */
const runsLoaded = ref(false)
const runsLoadError = ref('')
const trash = ref<RunOut[]>([])
const trashedSeries = ref<SeriesOut[]>([])
const trashLoaded = ref(false)
const trashLoadError = ref('')
type TrashItem = { kind: 'run' | 'series'; id: string; title: string }
/** What was just trashed, so the notice can offer to take it back. */
const lastTrashed = ref<TrashItem | null>(null)
const trashBusy = ref<string | null>(null)
const trashError = ref('')
const confirmStop = ref<{ kind: 'run' | 'series'; id: string } | null>(null)
const stopping = ref(false)
const stopError = ref('')

const folders = useRunFoldersStore()
const folderCounts = ref({ total: 0, root: 0 })
const dragging = ref(false)
const fileError = ref('')
type FolderPrompt =
  | { kind: 'create'; parentId: string | null }
  | { kind: 'rename'; folder: RunFolderOut }
  | { kind: 'move'; folder: RunFolderOut }
  | { kind: 'remove'; folder: RunFolderOut }
const prompt = ref<FolderPrompt | null>(null)
const draftName = ref('')
const draftParent = ref<string | null>(null)
const promptBusy = ref(false)
const promptError = ref('')
type Item = { kind: 'run'; row: RunOut } | { kind: 'series'; row: SeriesOut }
const renaming = ref<Item | null>(null)
const filing = ref<Item | null>(null)
const draftFolder = ref<string | null>(null)
const filingBusy = ref(false)
const filingError = ref('')
let disposed = false
let timer: ReturnType<typeof setInterval> | null = null
let loadEpoch = 0

function when(value: string): string {
  return new Date(value).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}

const tab = computed<'runs' | 'trash'>(() => (route.query.tab === 'trash' ? 'trash' : 'runs'))

function selectTab(next: 'runs' | 'trash'): void {
  if (next === tab.value) return
  const query = { ...route.query }
  if (next === 'runs') delete query.tab
  else query.tab = next
  void router.replace({ name: 'runs', query })
}

/** `null` shows everything, `'root'` what is in no folder, else a folder id. */
const selected = computed<string | null>(() =>
  typeof route.query.folder === 'string' && route.query.folder ? route.query.folder : null,
)

function selectFolder(next: string | null): void {
  if (next === selected.value) return
  const query = { ...route.query }
  if (next === null) delete query.folder
  else query.folder = next
  void router.replace({ name: 'runs', query })
}

const breadcrumb = computed(() =>
  selected.value && selected.value !== ROOT ? folders.pathOf(selected.value) : [],
)

/** Folders a folder may move into: everything except itself and its subtree. */
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

const seriesById = computed(() => new Map(series.value.map((row) => [row.id, row])))
const trashCount = computed(() => trash.value.length + trashedSeries.value.length)

function seriesTone(status: string): string {
  if (status === 'failed') return 'badge--fail'
  if (status === 'completed') return 'badge--pass'
  if (status === 'stopped') return 'badge--idle'
  return 'badge--mark'
}

/** A series that is not being worked on goes to the trash with all its episodes. */
function seriesTrashable(row: SeriesOut | undefined): row is SeriesOut {
  return !!row && !LIVE_SERIES.has(row.status) && !row.active
}

/** A run not in progress can go to the trash; an episode only with its series. */
function trashable(run: RunOut): boolean {
  if (run.series_id) return seriesTrashable(seriesById.value.get(run.series_id))
  return !STOPPABLE_RUN.has(run.status) && !run.active
}

async function loadTrash(): Promise<void> {
  try {
    const [trashedRuns, trashedRows] = await Promise.all([runs.trashed(), seriesStore.trashed()])
    trash.value = trashedRuns
    trashedSeries.value = trashedRows
    trashLoaded.value = true
    trashLoadError.value = ''
  } catch (exc) {
    trashLoadError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  }
}

async function load(): Promise<void> {
  const ticket = ++loadEpoch
  const folder = selected.value ?? undefined
  let failure: unknown
  try {
    await runs.load(undefined, undefined, folder)
  } catch (exc) {
    failure = exc
  }
  if (ticket !== loadEpoch) return
  if (failure) {
    runsLoadError.value = failure instanceof ApiError ? failure.detail : t.errors.generic
  } else {
    runsLoaded.value = true
    runsLoadError.value = ''
  }
  const nextSeries = await seriesStore.list(undefined, folder).catch(() => [])
  if (ticket !== loadEpoch) return
  series.value = nextSeries
  const moving =
    runs.items.some((run) => LIVE_RUN.has(run.status) || run.active) ||
    series.value.some((row) => LIVE_SERIES.has(row.status) || row.active)
  if (!disposed && moving && !timer) timer = setInterval(load, POLL_MS)
  if (!moving && timer) {
    clearInterval(timer)
    timer = null
  }
}

function askStop(kind: 'run' | 'series', id: string): void {
  stopError.value = ''
  confirmStop.value = { kind, id }
}

async function confirm(): Promise<void> {
  const target = confirmStop.value
  if (!target || stopping.value) return
  stopping.value = true
  stopError.value = ''
  try {
    if (target.kind === 'run') await runs.stop(target.id)
    else await seriesStore.stop(target.id)
    confirmStop.value = null
    await load()
  } catch (exc) {
    stopError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    stopping.value = false
  }
}

async function moveToTrash(run: RunOut): Promise<void> {
  const row = run.series_id ? seriesById.value.get(run.series_id) : undefined
  if (row) return moveSeriesToTrash(row)
  if (trashBusy.value) return
  trashBusy.value = run.id
  trashError.value = ''
  try {
    const trashed = await runs.trash(run.id)
    lastTrashed.value = { kind: 'run', id: trashed.id, title: runTitle(trashed) }
    await Promise.all([load(), loadTrash(), loadFolders()])
  } catch (exc) {
    trashError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    trashBusy.value = null
  }
}

async function moveSeriesToTrash(row: SeriesOut): Promise<void> {
  if (trashBusy.value) return
  trashBusy.value = row.id
  trashError.value = ''
  try {
    const trashed = await seriesStore.trash(row.id)
    lastTrashed.value = { kind: 'series', id: trashed.id, title: seriesTitle(trashed) }
    await Promise.all([load(), loadTrash(), loadFolders()])
  } catch (exc) {
    trashError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    trashBusy.value = null
  }
}

async function restore(kind: TrashItem['kind'], id: string): Promise<void> {
  if (trashBusy.value) return
  trashBusy.value = id
  trashError.value = ''
  try {
    if (kind === 'series') await seriesStore.restore(id)
    else await runs.restore(id)
    if (lastTrashed.value?.id === id) lastTrashed.value = null
    await Promise.all([load(), loadTrash(), loadFolders()])
  } catch (exc) {
    trashError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    trashBusy.value = null
  }
}

/** The folder rows and the counts of "all" and "no folder". */
async function loadFolders(): Promise<void> {
  try {
    const [, counts] = await Promise.all([folders.load(), runFolderSummary()])
    folderCounts.value = counts
  } catch (exc) {
    fileError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  }
}

function itemTitle(item: Item): string {
  return item.kind === 'series' ? seriesTitle(item.row) : runTitle(item.row)
}

function startDrag(event: DragEvent, item: Item): void {
  event.dataTransfer?.setData(DRAG_TYPE, `${item.kind}:${item.row.id}`)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
  dragging.value = true
}

function moveItem(kind: string, id: string, folderId: string | null): Promise<unknown> {
  return kind === 'series' ? seriesStore.move(id, folderId) : runs.move(id, folderId)
}

/** A drop on the sidebar: file each dragged row, a series with all its episodes. */
async function fileDropped(keys: string[], folderId: string | null): Promise<void> {
  dragging.value = false
  fileError.value = ''
  try {
    for (const key of keys) {
      const [kind, id] = key.split(':')
      if (id) await moveItem(kind, id, folderId)
    }
  } catch (exc) {
    fileError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  }
  await Promise.all([load(), loadFolders()])
}

function openFiling(item: Item): void {
  filing.value = item
  filingError.value = ''
  draftFolder.value = item.row.folder_id ?? null
}

async function confirmFiling(): Promise<void> {
  const item = filing.value
  if (!item || filingBusy.value) return
  filingBusy.value = true
  filingError.value = ''
  try {
    await moveItem(item.kind, item.row.id, draftFolder.value)
    filing.value = null
    await Promise.all([load(), loadFolders()])
  } catch (exc) {
    filingError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    filingBusy.value = false
  }
}

function saveName(name: string): Promise<unknown> {
  const item = renaming.value
  if (!item) return Promise.resolve()
  return item.kind === 'series' ? seriesStore.rename(item.row.id, name) : runs.rename(item.row.id, name)
}

function openPrompt(next: FolderPrompt): void {
  prompt.value = next
  draftName.value = next.kind === 'rename' ? next.folder.name : ''
  promptError.value = ''
  draftParent.value =
    next.kind === 'create' ? next.parentId : next.kind === 'move' ? next.folder.parent_id : null
}

async function confirmPrompt(): Promise<void> {
  const current = prompt.value
  if (!current || promptBusy.value) return
  promptBusy.value = true
  promptError.value = ''
  try {
    if (current.kind === 'create') await folders.create(draftName.value, draftParent.value)
    if (current.kind === 'rename') await folders.rename(current.folder.id, draftName.value)
    if (current.kind === 'move') await folders.move(current.folder.id, draftParent.value)
    if (current.kind === 'remove') {
      const gone = new Set([current.folder.id, ...folders.descendants(current.folder.id)])
      await folders.remove(current.folder.id, true)
      if (selected.value && gone.has(selected.value)) selectFolder(null)
      else await load()
      await loadFolders()
    }
    prompt.value = null
  } catch (exc) {
    promptError.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    promptBusy.value = false
  }
}

watch(selected, () => {
  // Another folder's rows are on the way: show the loading line, not the old rows.
  runsLoaded.value = false
  runsLoadError.value = ''
  runs.items = []
  series.value = []
  void load()
})

onMounted(() => Promise.all([load(), loadTrash(), loadFolders()]))
onUnmounted(() => {
  disposed = true
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="page">
    <header>
      <p class="eyebrow">{{ t.app.name }}</p>
      <h1 class="h-page">{{ t.nav.runs }}</h1>
    </header>

    <div class="tabs pipeline-tabs">
      <button class="tab" :class="{ 'tab--on': tab === 'runs' }" data-tab="runs" @click="selectTab('runs')">
        {{ t.run.tabRuns }}
        <span class="tab__count num">{{ runs.items.length }}</span>
      </button>
      <button class="tab" :class="{ 'tab--on': tab === 'trash' }" data-tab="trash" @click="selectTab('trash')">
        {{ t.run.tabTrash }}
        <span class="tab__count num">{{ trashCount }}</span>
      </button>
    </div>

    <div v-if="lastTrashed" class="notice trashed" role="status" data-trashed-notice>
      <span>
        {{ fill(lastTrashed.kind === 'series' ? t.series.trashedNotice : t.run.trashedNotice, { title: lastTrashed.title }) }}
      </span>
      <button
        class="btn btn--sm"
        type="button"
        data-undo
        :disabled="trashBusy !== null"
        @click="restore(lastTrashed.kind, lastTrashed.id)"
      >
        {{ t.run.undo }}
      </button>
    </div>
    <p v-if="trashError" class="notice notice--fail" role="alert">{{ trashError }}</p>

    <template v-if="tab === 'trash'">
      <p class="muted lead">{{ t.run.trashLead }}</p>
      <p v-if="trashLoadError" class="notice notice--fail" role="alert" data-trash-load-error>
        {{ trashLoadError }}
      </p>
      <LoadingState v-if="!trashLoaded && !trashLoadError" class="empty" />
      <p v-if="trashLoaded && !trashLoadError && !trashCount" class="muted empty">{{ t.run.trashEmpty }}</p>
      <table v-if="trashedSeries.length" class="runs" data-trash-series-list>
        <thead>
          <tr>
            <th>{{ t.series.title }}</th>
            <th>{{ t.run.flow }}</th>
            <th>Status</th>
            <th class="right">{{ t.common.cost }}</th>
            <th class="right">{{ t.run.trashedAt }}</th>
            <th class="right">{{ t.common.page }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in trashedSeries" :key="row.id">
            <td>
              <RouterLink :to="{ name: 'series', params: { id: row.id } }" class="link">
                {{ seriesTitle(row) }}
              </RouterLink>
              <span class="meta block">{{ row.document_title }} · {{ when(row.created_at) }}</span>
            </td>
            <td class="meta">{{ row.flow_id }} v{{ row.flow_version }}</td>
            <td>
              <span class="badge" :class="seriesTone(row.status)">{{ t.series.status[row.status] }}</span>
            </td>
            <td class="right num">${{ row.total_cost_usd.toFixed(4) }}</td>
            <td class="right meta">{{ row.trashed_at ? when(row.trashed_at) : t.common.none }}</td>
            <td class="right">
              <button
                class="btn btn--sm"
                type="button"
                data-restore-series
                :disabled="trashBusy !== null"
                @click="restore('series', row.id)"
              >
                {{ t.run.restore }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <table v-if="trash.length" class="runs" data-trash-list>
        <thead>
          <tr>
            <th>{{ t.run.document }}</th>
            <th>{{ t.run.flow }}</th>
            <th>Status</th>
            <th class="right">{{ t.common.cost }}</th>
            <th class="right">{{ t.run.trashedAt }}</th>
            <th class="right">{{ t.common.page }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in trash" :key="run.id">
            <td>
              <RouterLink :to="{ name: 'run', params: { id: run.id } }" class="link">
                {{ runTitle(run) }}
              </RouterLink>
              <span class="meta block">
                <template v-if="run.name && run.document_title">{{ run.document_title }} · </template>{{ when(run.created_at) }}
              </span>
            </td>
            <td class="meta">{{ run.flow_id }} v{{ run.flow_version }}</td>
            <td><StatusPill :status="run.status" /></td>
            <td class="right num">${{ run.total_cost_usd.toFixed(4) }}</td>
            <td class="right meta">{{ run.trashed_at ? when(run.trashed_at) : t.common.none }}</td>
            <td class="right">
              <button
                class="btn btn--sm"
                type="button"
                data-restore
                :disabled="trashBusy !== null"
                @click="restore('run', run.id)"
              >
                {{ t.run.restore }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <div v-else class="layout">
      <FolderTree
        :folders="folders.items"
        :selected="selected"
        :dropping="dragging"
        :total-count="folderCounts.total"
        :root-count="folderCounts.root"
        :count-of="(folder) => folder.total_run_count"
        :drag-type="DRAG_TYPE"
        :labels="{ title: t.folders.title, all: t.run.allRuns, root: t.folders.root }"
        @select="selectFolder"
        @drop-items="fileDropped($event.ids, $event.folderId)"
        @create="openPrompt({ kind: 'create', parentId: $event })"
        @rename="openPrompt({ kind: 'rename', folder: $event })"
        @move="openPrompt({ kind: 'move', folder: $event })"
        @remove="openPrompt({ kind: 'remove', folder: $event })"
      />

      <div class="pane">
        <p v-if="breadcrumb.length" class="crumbs meta" data-breadcrumb>
          <span v-for="(step, index) in breadcrumb" :key="step.id">
            <button class="crumb" type="button" @click="selectFolder(step.id)">{{ step.name }}</button>
            <span v-if="index < breadcrumb.length - 1" aria-hidden="true"> / </span>
          </span>
        </p>
        <p v-if="fileError" class="notice notice--fail" role="alert" data-file-error>{{ fileError }}</p>
        <p v-if="runsLoadError" class="notice notice--fail" role="alert" data-runs-load-error>
          {{ runsLoadError }}
        </p>
        <table v-if="series.length" class="runs" data-series-list>
          <thead>
            <tr>
              <th>{{ t.series.title }}</th>
              <th>{{ t.run.flow }}</th>
              <th>Status</th>
              <th class="right">{{ t.common.cost }}</th>
              <th class="right">{{ t.series.count }}</th>
              <th class="right">{{ t.common.page }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in series"
              :key="row.id"
              draggable="true"
              :data-series-row="row.id"
              @dragstart="startDrag($event, { kind: 'series', row })"
              @dragend="dragging = false"
            >
              <td>
                <RouterLink :to="{ name: 'series', params: { id: row.id } }" class="link">
                  {{ seriesTitle(row) }}
                </RouterLink>
                <span class="meta block">{{ row.document_title }} · {{ when(row.created_at) }}</span>
              </td>
              <td class="meta">{{ row.flow_id }} v{{ row.flow_version }}</td>
              <td>
                <span class="badge" :class="seriesTone(row.status)">
                  {{ t.series.status[row.status] }}
                </span>
                <button
                  v-if="LIVE_SERIES.has(row.status)"
                  class="btn btn--sm"
                  type="button"
                  data-stop-series
                  @click="askStop('series', row.id)"
                >
                  {{ t.run.stop }}
                </button>
              </td>
              <td class="right num">${{ row.total_cost_usd.toFixed(4) }}</td>
              <td class="right num">{{ row.episodes.length || row.request.episodes || t.common.none }}</td>
              <td class="right">
                <span class="row-actions">
                  <button
                    class="btn btn--sm btn--ghost"
                    type="button"
                    data-rename-series
                    @click="renaming = { kind: 'series', row }"
                  >
                    {{ t.common.rename }}
                  </button>
                  <button
                    class="btn btn--sm btn--ghost"
                    type="button"
                    data-file-series
                    :title="t.series.moveTitle"
                    @click="openFiling({ kind: 'series', row })"
                  >
                    {{ t.folders.inFolder }}
                  </button>
                  <button
                    v-if="seriesTrashable(row)"
                    class="btn btn--sm btn--ghost btn--danger"
                    type="button"
                    data-trash-series
                    :title="t.series.trash"
                    :disabled="trashBusy !== null"
                    @click="moveSeriesToTrash(row)"
                  >
                    {{ t.run.delete }}
                  </button>
                </span>
              </td>
            </tr>
          </tbody>
        </table>

        <LoadingState v-if="!runsLoaded && !runsLoadError && !runs.items.length" class="empty" />
        <p
          v-else-if="runsLoaded && !runsLoadError && !runs.items.length && !(selected && series.length)"
          class="muted empty"
          data-runs-empty
        >
          {{ selected ? t.folders.empty : t.run.empty }}
        </p>

        <table v-else-if="runs.items.length" class="runs" data-run-list>
          <thead>
            <tr>
              <th>{{ t.run.document }}</th>
              <th>{{ t.run.flow }}</th>
              <th>Status</th>
              <th class="right">{{ t.common.cost }}</th>
              <th class="right">{{ t.run.targetMinutes }}</th>
              <th class="right">{{ t.common.page }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="run in runs.items"
              :key="run.id"
              draggable="true"
              :data-run-row="run.id"
              @dragstart="startDrag($event, { kind: 'run', row: run })"
              @dragend="dragging = false"
            >
              <td>
                <RouterLink :to="{ name: 'run', params: { id: run.id } }" class="link">
                  {{ runTitle(run) }}
                </RouterLink>
                <span class="meta block">
                  <template v-if="run.name && run.document_title">{{ run.document_title }} · </template>{{ when(run.created_at) }}
                </span>
                <RouterLink
                  v-if="run.series_id && run.episode_index"
                  class="badge badge--mark series-badge"
                  :to="{ name: 'series', params: { id: run.series_id } }"
                >
                  {{ fill(t.run.seriesBadge, { n: run.episode_index }) }}
                </RouterLink>
              </td>
              <td class="meta">{{ run.flow_id }} v{{ run.flow_version }}</td>
              <td>
                <StatusPill :status="run.status" />
                <button
                  v-if="STOPPABLE_RUN.has(run.status)"
                  class="btn btn--sm"
                  type="button"
                  data-stop
                  @click="askStop('run', run.id)"
                >
                  {{ t.run.stop }}
                </button>
              </td>
              <td class="right num">${{ run.total_cost_usd.toFixed(4) }}</td>
              <td class="right num">{{ run.target_minutes ?? t.common.none }}</td>
              <td class="right">
                <span class="row-actions">
                  <RouterLink
                    v-if="['completed', 'in_review', 'reviewed'].includes(run.status)"
                    class="btn btn--sm"
                    :to="{ name: 'review', params: { id: run.id } }"
                  >
                    {{ t.review.title }}
                  </RouterLink>
                  <button
                    class="btn btn--sm btn--ghost"
                    type="button"
                    data-rename
                    @click="renaming = { kind: 'run', row: run }"
                  >
                    {{ t.common.rename }}
                  </button>
                  <button
                    class="btn btn--sm btn--ghost"
                    type="button"
                    data-file
                    :title="run.series_id ? t.series.moveTitle : t.run.moveTitle"
                    @click="openFiling({ kind: 'run', row: run })"
                  >
                    {{ t.folders.inFolder }}
                  </button>
                  <button
                    v-if="trashable(run)"
                    class="btn btn--sm btn--ghost btn--danger"
                    type="button"
                    data-trash
                    :title="run.series_id ? t.series.trash : t.run.trash"
                    :disabled="trashBusy !== null"
                    @click="moveToTrash(run)"
                  >
                    {{ t.run.delete }}
                  </button>
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <RenameDialog
      :open="renaming !== null"
      :title="renaming?.kind === 'series' ? t.series.renameTitle : t.run.renameTitle"
      :lead="renaming?.kind === 'series' ? '' : t.run.renameLead"
      :hint="renaming?.kind === 'series' ? t.series.nameHint : t.run.nameHint"
      :current="renaming?.row.name"
      :save="saveName"
      @saved="load"
      @close="renaming = null"
    />

    <ModalDialog
      :open="filing !== null"
      :title="filing?.kind === 'series' || filing?.row.series_id ? t.series.moveTitle : t.run.moveTitle"
      :lead="
        filing?.kind === 'series'
          ? t.series.moveLead
          : filing?.row.series_id
            ? t.run.moveEpisodeLead
            : t.run.moveLead
      "
      @close="filing = null"
    >
      <p v-if="filing" class="quote">{{ itemTitle(filing) }}</p>
      <div class="field">
        <label for="run-folder">{{ t.folders.inFolder }}</label>
        <select id="run-folder" v-model="draftFolder" class="select" data-autofocus data-file-target>
          <option :value="null">{{ t.folders.root }}</option>
          <option v-for="folder in folders.items" :key="folder.id" :value="folder.id">
            {{ folder.path.join(' / ') }}
          </option>
        </select>
      </div>
      <p v-if="filingError" class="notice notice--fail" role="alert">{{ filingError }}</p>
      <template #actions>
        <button class="btn" type="button" @click="filing = null">{{ t.common.cancel }}</button>
        <button
          class="btn btn--mark"
          type="button"
          data-action="confirm-file"
          :disabled="filingBusy"
          @click="confirmFiling"
        >
          {{ filingBusy ? t.common.saving : t.common.save }}
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="prompt !== null"
      :title="promptTitle"
      :lead="prompt?.kind === 'remove' ? t.run.folderDeleteLead : ''"
      @close="prompt = null"
    >
      <div class="stack">
        <p v-if="prompt?.kind === 'remove'" class="quote">{{ prompt.folder.name }}</p>

        <div v-if="prompt?.kind === 'create' || prompt?.kind === 'rename'" class="field">
          <label for="run-folder-name">{{ t.folders.name }}</label>
          <input
            id="run-folder-name"
            v-model="draftName"
            class="input"
            data-autofocus
            data-folder-name
            @keydown.enter.prevent="draftName.trim() && confirmPrompt()"
          />
        </div>

        <p v-if="promptError" class="notice notice--fail" role="alert">{{ promptError }}</p>

        <div v-if="prompt?.kind === 'create' || prompt?.kind === 'move'" class="field">
          <label for="run-folder-parent">{{ t.folders.parent }}</label>
          <select id="run-folder-parent" v-model="draftParent" class="select">
            <option :value="null">{{ t.folders.root }}</option>
            <option v-for="folder in destinations" :key="folder.id" :value="folder.id">
              {{ folder.path.join(' / ') }}
            </option>
          </select>
        </div>
      </div>

      <template #actions>
        <button class="btn" type="button" @click="prompt = null">{{ t.common.cancel }}</button>
        <button
          class="btn"
          type="button"
          data-action="confirm-folder"
          :class="prompt?.kind === 'remove' ? 'btn--danger' : 'btn--mark'"
          :disabled="
            promptBusy ||
            ((prompt?.kind === 'create' || prompt?.kind === 'rename') && !draftName.trim())
          "
          @click="confirmPrompt"
        >
          {{ prompt?.kind === 'remove' ? t.common.delete : promptBusy ? t.common.saving : t.common.save }}
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="confirmStop !== null"
      :title="confirmStop?.kind === 'series' ? t.series.stopTitle : t.run.stopTitle"
      :lead="confirmStop?.kind === 'series' ? t.series.stopLead : t.run.stopLead"
      @close="confirmStop = null"
    >
      <p v-if="stopError" class="notice notice--fail" role="alert">{{ stopError }}</p>
      <template #actions>
        <button class="btn btn--ghost" type="button" @click="confirmStop = null">
          {{ t.common.cancel }}
        </button>
        <button
          class="btn btn--mark"
          type="button"
          data-action="confirm-stop"
          :disabled="stopping"
          @click="confirm"
        >
          {{ t.run.stopConfirm }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.page {
  padding: var(--s6);
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s5);
}

.layout {
  display: grid;
  grid-template-columns: var(--sidebar) minmax(0, 1fr);
  gap: var(--s4);
  align-items: start;
}

.pane {
  display: flex;
  flex-direction: column;
  gap: var(--s5);
  min-width: 0;
}

.crumbs {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  margin: 0;
}

.crumb {
  border: 0;
  background: transparent;
  padding: 0;
  color: var(--ink-2);
  font: inherit;
  cursor: pointer;
}

.crumb:hover {
  color: var(--mark);
}

.runs tbody tr[draggable='true'] {
  cursor: grab;
}

.quote {
  margin: 0 0 var(--s3);
  padding: var(--s3);
  border-radius: var(--r-md);
  background: var(--chrome);
  font-size: var(--t-sm);
}

.runs {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--t-sm);
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  overflow: hidden;
}

.runs th {
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
}

.runs td {
  padding: var(--s3) var(--s4);
  border-bottom: 1px solid var(--rule);
  vertical-align: middle;
}

.runs tbody tr:last-child td {
  border-bottom: 0;
}

.runs tbody tr:hover {
  background: var(--chrome);
}

.right {
  text-align: right;
}

.link {
  color: var(--ink);
  font-weight: 550;
  text-decoration: none;
}

.link:hover {
  color: var(--mark);
}

.block {
  display: block;
  margin-top: 2px;
}

.btn {
  text-decoration: none;
}

.series-badge {
  text-decoration: none;
  margin-top: 4px;
}

.empty {
  padding: var(--s8);
  text-align: center;
}

.loading.empty {
  justify-content: center;
}

.lead {
  margin: 0;
  font-size: var(--t-sm);
}

.row-actions {
  display: inline-flex;
  gap: var(--s2);
  justify-content: flex-end;
}

.notice {
  margin: 0;
  padding: var(--s3) var(--s4);
  border-radius: var(--r-md);
  font-size: var(--t-sm);
}

.notice--fail {
  background: var(--fail-soft);
  color: var(--fail);
}

.trashed {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s3);
  flex-wrap: wrap;
  background: var(--sunk);
  color: var(--ink-2);
}
</style>
