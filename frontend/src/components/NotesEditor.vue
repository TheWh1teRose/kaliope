<script setup lang="ts">
/**
 * Notes on an outline or a script, in the Redaktion layout.
 *
 * The left list is segments grouped by beat. Notes sit on the beat or the
 * segment they name. A plus opens a popup to write or edit one.
 */
import { computed, onMounted, ref } from 'vue'

import type {
  AnchorOut,
  AnchorRect,
  BeatOut,
  FeedbackCitation,
  FeedbackOut,
  Note,
  NoteTargetKind,
  StructureOut,
} from '@/api/types'
import ModalDialog from '@/components/ModalDialog.vue'
import SourceRegister from '@/components/SourceRegister.vue'
import { t } from '@/i18n'
import { useDocumentsStore } from '@/stores/documents'

const props = defineProps<{
  feedback: FeedbackOut
  notes: Note[]
  busy?: boolean
}>()

const emit = defineEmits<{
  (event: 'update:notes', notes: Note[]): void
  (event: 'save'): void
  (event: 'submit'): void
}>()

const documents = useDocumentsStore()

const structure = ref<StructureOut | null>(null)
const selectedKind = ref<NoteTargetKind | null>(null)
const selectedId = ref<string | null>(null)
const activeAnchor = ref<AnchorOut | null>(null)
const dialog = ref<{ kind: NoteTargetKind; id: string; note: Note | null } | null>(null)
const draft = ref('')

const beats = computed(() => props.feedback.outline?.beats ?? [])
const citations = computed(() => props.feedback.citations ?? [])
const fallbackSegments = computed(() => props.feedback.script?.segments ?? [])
const segments = computed<FeedbackCitation[]>(() => {
  if (citations.value.length) return citations.value
  return fallbackSegments.value.map((segment) => ({
    id: segment.id,
    speaker: segment.speaker,
    text: segment.text,
    kind: segment.kind,
    beat_id: segment.beat_id,
    anchors: [],
  }))
})

interface Group {
  beat: BeatOut | null
  beatId: string
  title: string
  summary: string | null
  segments: FeedbackCitation[]
}

const groups = computed<Group[]>(() => {
  const used = new Set<string>()
  const items: Group[] = []
  for (const beat of beats.value) {
    const members = segments.value.filter((segment) => segment.beat_id === beat.id)
    members.forEach((segment) => used.add(segment.id))
    items.push({
      beat,
      beatId: beat.id,
      title: beat.title,
      summary: beat.summary,
      segments: members,
    })
  }
  const leftover = segments.value.filter((segment) => !used.has(segment.id))
  if (!beats.value.length && leftover.length) {
    const byBeat = new Map<string, FeedbackCitation[]>()
    for (const segment of leftover) {
      const key = segment.beat_id || '_'
      const list = byBeat.get(key) ?? []
      list.push(segment)
      byBeat.set(key, list)
    }
    for (const [beatId, members] of byBeat) {
      items.push({
        beat: null,
        beatId,
        title: beatId === '_' ? t.notes.ungrouped : beatId,
        summary: null,
        segments: members,
      })
    }
    return items
  }
  if (leftover.length) {
    items.push({
      beat: null,
      beatId: '_',
      title: t.notes.ungrouped,
      summary: null,
      segments: leftover,
    })
  }
  return items
})

const allNotes = computed(() => [...props.feedback.existing_notes, ...props.notes])

const notesByTarget = computed(() => {
  const map = new Map<string, Note[]>()
  for (const note of allNotes.value) {
    const key = `${note.target.kind}:${note.target.id}`
    const list = map.get(key) ?? []
    list.push(note)
    map.set(key, list)
  }
  return map
})

const highlight = computed<AnchorRect[]>(() => {
  if (activeAnchor.value) return activeAnchor.value.rects
  if (selectedKind.value === 'beat' && selectedId.value) {
    const beat = beats.value.find((item) => item.id === selectedId.value)
    if (!beat || !structure.value) return []
    return structure.value.blocks
      .filter((block) => beat.block_ids.includes(block.id))
      .flatMap((block) => block.bboxes.map(([page, bbox]) => ({ page, bbox })))
  }
  if (selectedKind.value === 'segment' && selectedId.value) {
    return segments.value.find((item) => item.id === selectedId.value)?.anchors[0]?.rects ?? []
  }
  return []
})

const dialogTitle = computed(() => (dialog.value?.note ? t.notes.editTitle : t.notes.addTitle))

function keyOf(kind: NoteTargetKind, id: string): string {
  return `${kind}:${id}`
}

function notesOn(kind: NoteTargetKind, id: string): Note[] {
  return notesByTarget.value.get(keyOf(kind, id)) ?? []
}

function isDraft(note: Note): boolean {
  return props.notes.some((item) => item.id === note.id)
}

function select(kind: NoteTargetKind, id: string): void {
  selectedKind.value = kind
  selectedId.value = id
  if (kind === 'segment') {
    activeAnchor.value = segments.value.find((item) => item.id === id)?.anchors[0] ?? null
  } else {
    activeAnchor.value = null
  }
}

function showAnchor(segmentId: string, anchor: AnchorOut): void {
  select('segment', segmentId)
  activeAnchor.value = anchor
}

function openAdd(kind: NoteTargetKind, id: string): void {
  select(kind, id)
  dialog.value = { kind, id, note: null }
  draft.value = ''
}

function openEdit(note: Note): void {
  if (!isDraft(note)) return
  select(note.target.kind, note.target.id)
  dialog.value = { kind: note.target.kind, id: note.target.id, note }
  draft.value = note.text
}

function closeDialog(): void {
  dialog.value = null
  draft.value = ''
}

function saveDialog(): void {
  const text = draft.value.trim()
  if (!text || !dialog.value) return
  if (dialog.value.note) {
    emit(
      'update:notes',
      props.notes.map((note) => (note.id === dialog.value?.note?.id ? { ...note, text } : note)),
    )
  } else {
    emit('update:notes', [
      ...props.notes,
      {
        id: `n${Math.random().toString(16).slice(2, 12)}`,
        text,
        target: { kind: dialog.value.kind, id: dialog.value.id },
        source: 'human',
        criterion: null,
      },
    ])
  }
  closeDialog()
}

function removeNote(id: string): void {
  emit(
    'update:notes',
    props.notes.filter((note) => note.id !== id),
  )
  closeDialog()
}

function sourceLabel(source: string): string {
  return source === 'ai_critic' ? t.notes.sourceAi : t.notes.sourceHuman
}

function speakerIndex(name: string): number {
  const names = [...new Set(segments.value.map((item) => item.speaker))]
  const index = names.indexOf(name)
  return index < 0 ? 0 : index
}

onMounted(async () => {
  if (props.feedback.document_id) {
    structure.value = await documents.structure(props.feedback.document_id)
  }
})
</script>

<template>
  <div class="notes">
    <header class="bar">
      <div class="grow">
        <p class="eyebrow">{{ t.notes.title }}</p>
        <p v-if="feedback.instructions" class="muted">{{ feedback.instructions }}</p>
      </div>
      <button class="btn btn--sm btn--ghost" :disabled="busy" @click="emit('save')">
        {{ t.notes.saveDraft }}
      </button>
      <button class="btn btn--sm btn--primary" :disabled="busy" @click="emit('submit')">
        {{ busy ? t.notes.submitting : t.notes.submit }}
      </button>
    </header>

    <SourceRegister
      :document-id="feedback.document_id"
      :structure="structure"
      :highlight="highlight"
      :active-segment-id="selectedId"
    >
      <p class="eyebrow pane__label">
        {{ feedback.subject === 'outline' ? t.notes.outline : t.notes.script }}
      </p>
      <p v-if="!groups.length" class="muted">{{ t.notes.noSubject }}</p>

      <section v-for="group in groups" :key="group.beatId" class="group">
        <header
          class="group__head"
          :class="{ 'group__head--on': selectedKind === 'beat' && selectedId === group.beatId }"
          :data-segment="group.beatId"
          @click="select('beat', group.beatId)"
        >
          <div class="grow">
            <p class="eyebrow">{{ t.notes.beat }}</p>
            <h2 class="group__title">{{ group.title }}</h2>
            <p v-if="group.summary" class="muted">{{ group.summary }}</p>
          </div>
          <button
            class="plus"
            :title="t.notes.addToBeat"
            :disabled="busy"
            @click.stop="openAdd('beat', group.beatId)"
          >
            +
          </button>
        </header>

        <ul v-if="notesOn('beat', group.beatId).length" class="comments">
          <li v-for="note in notesOn('beat', group.beatId)" :key="note.id" class="comment">
            <p class="meta">
              {{ sourceLabel(note.source) }}
              <template v-if="note.criterion"> · {{ note.criterion }}</template>
            </p>
            <p class="prose">{{ note.text }}</p>
            <button v-if="isDraft(note)" class="linkish" @click.stop="openEdit(note)">
              {{ t.common.edit }}
            </button>
          </li>
        </ul>

        <article
          v-for="segment in group.segments"
          :key="segment.id"
          class="seg"
          :class="{
            'seg--on': selectedKind === 'segment' && selectedId === segment.id,
            'seg--claim': segment.kind === 'claim',
          }"
          :style="{ '--speaker': `var(--sp-${speakerIndex(segment.speaker)})` }"
          :data-segment="segment.id"
          @click="select('segment', segment.id)"
        >
          <header class="seg__head">
            <span class="seg__speaker">{{ segment.speaker }}</span>
            <span class="badge" :class="segment.kind === 'claim' ? 'badge--mark' : 'badge--idle'">
              {{ segment.kind === 'claim' ? t.review.claim : t.review.pedagogy }}
            </span>
            <span class="grow" />
            <button
              class="plus"
              :title="t.notes.addToSegment"
              :disabled="busy"
              @click.stop="openAdd('segment', segment.id)"
            >
              +
            </button>
          </header>
          <p class="seg__text prose">{{ segment.text }}</p>
          <ul v-if="notesOn('segment', segment.id).length" class="comments">
            <li v-for="note in notesOn('segment', segment.id)" :key="note.id" class="comment">
              <p class="meta">
                {{ sourceLabel(note.source) }}
                <template v-if="note.criterion"> · {{ note.criterion }}</template>
              </p>
              <p class="prose">{{ note.text }}</p>
              <button v-if="isDraft(note)" class="linkish" @click.stop="openEdit(note)">
                {{ t.common.edit }}
              </button>
            </li>
          </ul>
          <footer class="seg__foot">
            <div class="cites">
              <button
                v-for="(anchor, index) in segment.anchors"
                :key="index"
                class="cite"
                :class="{
                  'cite--on': activeAnchor === anchor,
                  'cite--broken': !anchor.resolved,
                }"
                :title="anchor.text"
                @click.stop="showAnchor(segment.id, anchor)"
              >
                <sup class="cite__n num">{{ index + 1 }}</sup>
                <span class="cite__id">{{ anchor.block_id }}</span>
              </button>
              <span v-if="!segment.anchors.length" class="meta">{{ t.review.noCitations }}</span>
            </div>
          </footer>
        </article>
      </section>
    </SourceRegister>

    <ModalDialog :open="Boolean(dialog)" :title="dialogTitle" @close="closeDialog">
      <div class="field">
        <label for="note-draft">{{ t.notes.text }}</label>
        <textarea
          id="note-draft"
          v-model="draft"
          class="textarea"
          rows="5"
          :placeholder="t.notes.placeholder"
          data-autofocus
        />
      </div>
      <template #actions>
        <button
          v-if="dialog?.note"
          class="btn btn--ghost btn--danger"
          :disabled="busy"
          @click="removeNote(dialog.note.id)"
        >
          {{ t.notes.remove }}
        </button>
        <span class="grow" />
        <button class="btn" @click="closeDialog">{{ t.common.cancel }}</button>
        <button class="btn btn--mark" :disabled="!draft.trim() || busy" @click="saveDialog">
          {{ t.common.save }}
        </button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.notes {
  --sp-0: #2d5bff;
  --sp-1: #0d9488;
  --sp-2: #9a3f6a;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.bar {
  display: flex;
  align-items: center;
  gap: var(--s3);
  padding: var(--s3) var(--s4);
  border-bottom: 1px solid var(--rule);
  background: var(--chrome);
}

.pane__label {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: var(--s2) 0;
  background: var(--press);
}

.group {
  display: grid;
  gap: var(--s3);
  padding-bottom: var(--s5);
}

.group__head {
  display: flex;
  align-items: flex-start;
  gap: var(--s3);
  padding: var(--s2) 0;
  border-bottom: 1px solid var(--rule);
  cursor: pointer;
}

.group__head--on .group__title {
  color: var(--mark);
}

.group__title {
  margin: 2px 0 0;
  font-size: var(--t-md);
  font-weight: 600;
}

.plus {
  width: 28px;
  height: 28px;
  border: 1px dashed var(--rule-strong);
  border-radius: 999px;
  background: transparent;
  color: var(--ink-3);
  font-size: 1.1rem;
  line-height: 1;
  cursor: pointer;
  flex-shrink: 0;
}

.plus:hover:not(:disabled) {
  border-color: var(--mark);
  border-style: solid;
  color: var(--mark);
}

.seg {
  border: 1px solid var(--rule);
  border-left: 3px solid var(--speaker, var(--rule-strong));
  border-radius: 0 var(--r-lg) var(--r-lg) 0;
  background: var(--card);
  padding: var(--s3) var(--s4);
  display: grid;
  gap: var(--s3);
  cursor: pointer;
}

.seg--on {
  box-shadow:
    0 0 0 1px var(--mark),
    var(--shadow-md);
}

.seg__head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.seg__speaker {
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--speaker);
}

.seg__text {
  margin: 0;
}

.seg__foot {
  display: flex;
  align-items: center;
  gap: var(--s3);
  flex-wrap: wrap;
  padding-top: var(--s2);
  border-top: 1px solid var(--rule);
}

.comments {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s2);
}

.comment {
  padding: var(--s2) var(--s3);
  border-left: 2px solid var(--rule-strong);
  background: var(--chrome);
  border-radius: 0 var(--r-md) var(--r-md) 0;
  display: grid;
  gap: 2px;
}

.cites {
  display: flex;
  gap: var(--s2);
  flex-wrap: wrap;
  align-items: center;
}

.cite {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  padding: 2px 7px;
  border: 1px solid var(--rule-strong);
  border-radius: 999px;
  background: var(--card);
  font-family: var(--mono);
  font-size: 0.625rem;
  color: var(--ink-2);
  cursor: pointer;
}

.cite:hover,
.cite--on {
  border-color: var(--mark);
  color: var(--mark-deep);
}

.cite--on {
  background: var(--mark);
  color: #fff;
}

.cite--broken {
  border-color: var(--fail);
  color: var(--fail);
}

.cite__n {
  font-size: 0.5625rem;
}

.linkish {
  background: none;
  border: 0;
  padding: 0;
  color: var(--ink-3);
  cursor: pointer;
  font: inherit;
  font-size: var(--t-xs);
  text-align: left;
}

.linkish:hover {
  color: var(--mark);
}
</style>
