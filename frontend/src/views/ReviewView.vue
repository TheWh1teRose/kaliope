<script setup lang="ts">
/**
 * The review workspace (§9.2).
 *
 * Left: the script. Right: the source page. Clicking a citation puts the two
 * in register — the page scrolls to the anchored rectangle, a crosshair parks
 * on it, and a tie line is drawn from the segment across the gutter. That
 * correspondence is the whole product, so it is the one thing the design
 * spends its boldness on.
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import type { AnchorOut, EditAction, ReviewSummary, RunOut, ScriptOut, StructureOut } from '@/api/types'
import ModalDialog from '@/components/ModalDialog.vue'
import ReasonPicker from '@/components/ReasonPicker.vue'
import SourceRegister from '@/components/SourceRegister.vue'
import { t } from '@/i18n'
import { useCatalogueStore } from '@/stores/catalogue'
import { useDocumentsStore } from '@/stores/documents'
import { useReviewStore } from '@/stores/review'
import { useRunsStore } from '@/stores/runs'

const props = defineProps<{ id: string }>()

const review = useReviewStore()
const runs = useRunsStore()
const documents = useDocumentsStore()
const catalogue = useCatalogueStore()
const router = useRouter()

const script = ref<ScriptOut | null>(null)
const run = ref<RunOut | null>(null)
const structure = ref<StructureOut | null>(null)

const activeSegmentId = ref<string | null>(null)
const activeAnchor = ref<AnchorOut | null>(null)

const dialog = ref<{ action: EditAction; segmentId: string } | null>(null)
const draftText = ref('')
const reasonCode = ref<string | null>(null)
const note = ref('')
const busy = ref(false)

const completing = ref(false)
const completeNote = ref('')
const summary = ref<ReviewSummary | null>(null)

/** Which segment has its tag input open, and what has been typed into it. */
const tagging = ref<string | null>(null)
const tagDraft = ref('')
const showOriginal = ref<Set<string>>(new Set())

const highlight = computed(() => activeAnchor.value?.rects ?? [])

const reviewed = computed(
  () => script.value?.segments.filter((s) => s.accepted || s.edited || s.flagged).length ?? 0,
)
const total = computed(() => script.value?.segments.length ?? 0)
const percent = computed(() => (total.value ? Math.round((reviewed.value / total.value) * 100) : 0))

const speakerIndex = computed(() => {
  const map = new Map<string, number>()
  script.value?.format_spec.speakers.forEach((s, index) => map.set(s.name, index))
  return map
})

const dialogSpec = computed(() => {
  switch (dialog.value?.action) {
    case 'edit':
      return { title: t.review.editTitle, lead: t.review.editLead, needsReason: true }
    case 'flag':
      return { title: t.review.flagTitle, lead: t.review.flagLead, needsReason: true }
    default:
      return { title: t.review.commentTitle, lead: '', needsReason: false }
  }
})

const canSave = computed(() => {
  if (!dialog.value) return false
  if (dialog.value.action === 'comment') return note.value.trim().length > 0
  if (!reasonCode.value) return false
  const spec = catalogue.reasonSpec(reasonCode.value)
  if (spec?.requires_note && !note.value.trim()) return false
  if (dialog.value.action === 'edit' && !draftText.value.trim()) return false
  return true
})

function segmentOf(id: string) {
  return script.value?.segments.find((s) => s.id === id) ?? null
}

function showAnchor(segmentId: string, anchor: AnchorOut): void {
  activeSegmentId.value = segmentId
  activeAnchor.value = anchor
}

async function accept(segmentId: string): Promise<void> {
  await review.record(props.id, { target_type: 'segment', target_id: segmentId, action: 'accept' })
  syncFromStore()
}

/**
 * Take back the most recent review action on a segment, one step at a time.
 *
 * The server owns what "the most recent action" means, because it is the fold
 * over the event stream that the export is also built from. Nothing is deleted:
 * the undo is itself an event.
 */
async function undo(segmentId: string): Promise<void> {
  busy.value = true
  try {
    await review.undo(props.id, segmentId)
    syncFromStore()
  } finally {
    busy.value = false
  }
}

async function addTag(segmentId: string): Promise<void> {
  const name = tagDraft.value.trim()
  if (!name) return
  await review.tag(props.id, segmentId, name)
  tagDraft.value = ''
  tagging.value = null
  syncFromStore()
}

async function removeTag(segmentId: string, name: string): Promise<void> {
  await review.untag(props.id, segmentId, name)
  syncFromStore()
}

function openTagInput(segmentId: string): void {
  tagging.value = tagging.value === segmentId ? null : segmentId
  tagDraft.value = ''
}

function toggleOriginal(segmentId: string): void {
  const next = new Set(showOriginal.value)
  if (!next.delete(segmentId)) next.add(segmentId)
  showOriginal.value = next
}

function commentTime(iso: string): string {
  return iso ? new Date(iso).toLocaleString('de-DE') : ''
}

function openDialog(action: EditAction, segmentId: string): void {
  dialog.value = { action, segmentId }
  draftText.value = segmentOf(segmentId)?.text ?? ''
  reasonCode.value = null
  note.value = ''
}

async function save(): Promise<void> {
  if (!dialog.value || !canSave.value) return
  const segment = segmentOf(dialog.value.segmentId)
  busy.value = true
  try {
    await review.record(props.id, {
      target_type: 'segment',
      target_id: dialog.value.segmentId,
      action: dialog.value.action,
      reason_code: reasonCode.value,
      note: note.value.trim() || null,
      text_before: segment?.text ?? null,
      text_after: dialog.value.action === 'edit' ? draftText.value.trim() : null,
    })
    syncFromStore()
    dialog.value = null
  } finally {
    busy.value = false
  }
}

function syncFromStore(): void {
  if (review.script) script.value = { ...review.script }
}

async function finish(): Promise<void> {
  busy.value = true
  try {
    summary.value = await review.complete(props.id, {}, completeNote.value.trim() || undefined)
    completing.value = false
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  ;[run.value, script.value] = await Promise.all([runs.get(props.id), review.open(props.id)])
  structure.value = await documents.structure(script.value.document_id)
})
</script>

<template>
  <div class="review">
    <header class="bar">
      <RouterLink :to="{ name: 'run', params: { id: props.id } }" class="eyebrow back">
        ← {{ t.run.title }}
      </RouterLink>
      <h1 class="bar__title truncate">{{ run?.document_title || t.review.title }}</h1>

      <div class="progress" :title="`${reviewed}/${total}`">
        <div class="progress__fill" :style="{ width: `${percent}%` }" />
      </div>
      <span class="meta num">{{ reviewed }}/{{ total }} {{ t.review.progress }}</span>

      <span class="grow" />
      <a class="btn btn--sm" :href="`/api/runs/${props.id}/export?format=md`" download>
        {{ t.run.exportMd }}
      </a>
      <button class="btn btn--sm btn--primary" @click="completing = true">
        {{ t.review.complete }}
      </button>
    </header>

    <SourceRegister
      :document-id="script?.document_id ?? null"
      :structure="structure"
      :highlight="highlight"
      :active-segment-id="activeSegmentId"
    >
        <p class="eyebrow pane__label">{{ t.review.script }}</p>

        <article
          v-for="segment in script?.segments ?? []"
          :key="segment.id"
          class="seg"
          :class="{
            'seg--on': segment.id === activeSegmentId,
            'seg--accepted': segment.accepted,
            'seg--flagged': segment.flagged,
            'seg--claim': segment.kind === 'claim',
          }"
          :style="{ '--speaker': `var(--sp-${speakerIndex.get(segment.speaker) ?? 0})` }"
          :data-segment="segment.id"
        >
          <header class="seg__head">
            <span class="seg__speaker">{{ segment.speaker }}</span>
            <span class="badge" :class="segment.kind === 'claim' ? 'badge--mark' : 'badge--idle'">
              {{ segment.kind === 'claim' ? t.review.claim : t.review.pedagogy }}
            </span>
            <span v-if="segment.accepted" class="badge badge--pass">{{ t.review.accepted }}</span>
            <span v-if="segment.edited" class="badge badge--warn">{{ t.review.edited }}</span>
            <span v-if="segment.flagged" class="badge badge--fail">{{ t.review.flagged }}</span>
          </header>

          <p class="seg__text prose">{{ segment.text }}</p>

          <div v-if="segment.edited && segment.original_text" class="seg__original">
            <button class="linkish" @click="toggleOriginal(segment.id)">
              {{ showOriginal.has(segment.id) ? t.review.hideOriginal : t.review.showOriginal }}
            </button>
            <p v-if="showOriginal.has(segment.id)" class="quote prose">
              {{ segment.original_text }}
            </p>
          </div>

          <div class="seg__tags">
            <button
              v-for="tag in segment.tags"
              :key="tag"
              class="tag"
              :title="t.review.removeTag"
              @click="removeTag(segment.id, tag)"
            >
              {{ tag }}<span class="tag__x" aria-hidden="true">×</span>
            </button>

            <button class="tag tag--add" @click="openTagInput(segment.id)">
              + {{ t.review.addTag }}
            </button>

            <input
              v-if="tagging === segment.id"
              v-model="tagDraft"
              class="input input--tag"
              :placeholder="t.review.addTagPlaceholder"
              :list="`tags-${segment.id}`"
              @keydown.enter.prevent="addTag(segment.id)"
              @keydown.esc="tagging = null"
            />
            <datalist v-if="tagging === segment.id" :id="`tags-${segment.id}`">
              <option v-for="known in review.tags" :key="known" :value="known" />
            </datalist>
          </div>

          <div v-if="segment.comments.length" class="seg__comments">
            <p class="eyebrow">{{ t.review.comments }}</p>
            <article v-for="comment in segment.comments" :key="comment.id" class="comment">
              <p class="comment__meta meta">
                {{ comment.user_email ?? comment.user_id }} · {{ commentTime(comment.created_at) }}
              </p>
              <p class="comment__note">{{ comment.note }}</p>
            </article>
          </div>

          <div v-if="segment.violations.length" class="seg__findings">
            <p class="eyebrow">{{ t.review.gateFindings }}</p>
            <p v-for="(v, index) in segment.violations" :key="index" class="finding">
              <span class="badge" :class="`badge--${v.status === 'fail' ? 'fail' : 'warn'}`">
                {{ v.gate }}
              </span>
              {{ v.message }}
            </p>
          </div>

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
                @click="showAnchor(segment.id, anchor)"
              >
                <sup class="cite__n num">{{ index + 1 }}</sup>
                <span class="cite__id">{{ anchor.block_id }}</span>
                <span class="cite__range num">
                  {{ anchor.char_start }}–{{ anchor.char_end }}
                </span>
              </button>
              <span v-if="!segment.anchors.length" class="meta">{{ t.review.noCitations }}</span>
            </div>

            <div class="acts">
              <button
                class="btn btn--sm"
                :disabled="segment.accepted"
                @click="accept(segment.id)"
              >
                ✓ {{ t.review.accept }}
              </button>
              <button class="btn btn--sm btn--ghost" @click="openDialog('edit', segment.id)">
                ✎ {{ t.review.edit }}
              </button>
              <button
                class="btn btn--sm btn--ghost btn--danger"
                @click="openDialog('flag', segment.id)"
              >
                ⚑ {{ t.review.flag }}
              </button>
              <button class="btn btn--sm btn--ghost" @click="openDialog('comment', segment.id)">
                ✎̶ {{ t.review.comment }}
              </button>
              <button
                class="btn btn--sm btn--ghost"
                :disabled="!segment.undoable || busy"
                :title="t.review.undoTitle"
                @click="undo(segment.id)"
              >
                ↺ {{ t.review.undo }}
              </button>
            </div>
          </footer>
        </article>
    </SourceRegister>

    <ModalDialog
      :open="Boolean(dialog)"
      :title="dialogSpec.title"
      :lead="dialogSpec.lead"
      wide
      @close="dialog = null"
    >
      <div class="stack">
        <template v-if="dialog?.action === 'edit'">
          <div class="field">
            <label for="draft">{{ t.review.original }}</label>
            <p class="quote prose">{{ segmentOf(dialog.segmentId)?.text }}</p>
          </div>
          <div class="field">
            <label for="draft-text">{{ t.review.edit }}</label>
            <textarea id="draft-text" v-model="draftText" class="textarea" rows="6" />
          </div>
        </template>
        <p v-else-if="dialog" class="quote prose">{{ segmentOf(dialog.segmentId)?.text }}</p>

        <ReasonPicker
          v-if="dialogSpec.needsReason"
          v-model="reasonCode"
          :note="note"
          autofocus
          @update:note="note = $event"
        />
        <div v-else class="field">
          <label for="comment-note">{{ t.review.note }}</label>
          <textarea id="comment-note" v-model="note" class="textarea" rows="4" data-autofocus />
        </div>
      </div>

      <template #actions>
        <button class="btn" @click="dialog = null">{{ t.common.cancel }}</button>
        <button class="btn btn--mark" :disabled="!canSave || busy" @click="save">
          {{ t.common.save }}
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="completing"
      :title="t.review.completeTitle"
      :lead="t.review.completeLead"
      @close="completing = false"
    >
      <div class="field">
        <label for="complete-note">{{ t.review.completeNote }}</label>
        <textarea id="complete-note" v-model="completeNote" class="textarea" rows="3" data-autofocus />
      </div>
      <template #actions>
        <button class="btn" @click="completing = false">{{ t.common.cancel }}</button>
        <button class="btn btn--primary" :disabled="busy" @click="finish">
          {{ t.review.complete }}
        </button>
      </template>
    </ModalDialog>

    <ModalDialog
      :open="Boolean(summary)"
      :title="t.review.completed"
      @close="router.push({ name: 'run', params: { id: props.id } })"
    >
      <dl v-if="summary" class="summary">
        <div>
          <dt class="meta">{{ t.review.events }}</dt>
          <dd class="num">{{ summary.event_count }}</dd>
        </div>
        <div>
          <dt class="meta">{{ t.review.duration }}</dt>
          <dd class="num">{{ Math.round(summary.duration_seconds) }} s</dd>
        </div>
        <div v-for="(count, code) in summary.reason_code_counts" :key="code">
          <dt class="meta">{{ catalogue.reasonSpec(String(code))?.label_de ?? code }}</dt>
          <dd class="num">{{ count }}</dd>
        </div>
      </dl>
      <template #actions>
        <RouterLink class="btn btn--primary" :to="{ name: 'run', params: { id: props.id } }">
          {{ t.common.close }}
        </RouterLink>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.review {
  --sp-0: #2d5bff;
  --sp-1: #0d9488;
  --sp-2: #9a3f6a;
  display: flex;
  flex-direction: column;
  height: 100%;
}

.bar {
  display: flex;
  align-items: center;
  gap: var(--s3);
  padding: var(--s3) var(--s4);
  border-bottom: 1px solid var(--rule);
  background: var(--chrome);
}

.back {
  text-decoration: none;
  color: var(--ink-3);
  white-space: nowrap;
}

.bar__title {
  font-size: var(--t-md);
  font-weight: 600;
  letter-spacing: -0.01em;
  max-width: 28ch;
}

.progress {
  width: 120px;
  height: 4px;
  border-radius: 999px;
  background: var(--sunk);
  overflow: hidden;
}

.progress__fill {
  height: 100%;
  background: var(--mark);
  transition: width var(--slow);
}

.bar .btn {
  text-decoration: none;
}

.pane__label {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: var(--s2) 0;
  background: var(--press);
}

/* ------------------------------------------------------------- segments */

.seg {
  border: 1px solid var(--rule);
  border-left: 3px solid var(--speaker);
  border-radius: 0 var(--r-lg) var(--r-lg) 0;
  background: var(--card);
  padding: var(--s3) var(--s4);
  display: grid;
  gap: var(--s3);
  transition:
    border-color var(--fast),
    box-shadow var(--fast);
}

.seg--on {
  box-shadow:
    0 0 0 1px var(--mark),
    var(--shadow-md);
}

.seg--accepted {
  background: color-mix(in srgb, var(--pass-soft) 40%, var(--card));
}

.seg--flagged {
  background: color-mix(in srgb, var(--fail-soft) 35%, var(--card));
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

.seg__original {
  display: grid;
  gap: var(--s2);
  justify-items: start;
}

.linkish {
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--ink-3);
  font-size: var(--t-xs);
  cursor: pointer;
  text-decoration: underline dotted;
}

.linkish:hover {
  color: var(--mark);
}

/* --------------------------------------------------------------- tags */

.seg__tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
}

.tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border: 1px solid var(--rule-strong);
  border-radius: 999px;
  background: var(--chrome);
  color: var(--ink-2);
  font-size: var(--t-xs);
  cursor: pointer;
  transition:
    border-color var(--fast),
    color var(--fast);
}

.tag:hover {
  border-color: var(--fail);
  color: var(--fail);
}

.tag__x {
  color: var(--ink-4);
  font-size: 0.6875rem;
}

.tag:hover .tag__x {
  color: var(--fail);
}

.tag--add {
  border-style: dashed;
  color: var(--ink-3);
}

.tag--add:hover {
  border-color: var(--mark);
  border-style: solid;
  color: var(--mark);
}

.input--tag {
  width: 18ch;
  padding: 2px var(--s2);
  font-size: var(--t-xs);
}

/* ----------------------------------------------------------- comments */

.seg__comments {
  display: grid;
  gap: var(--s2);
  padding: var(--s2) var(--s3);
  border-left: 2px solid var(--rule-strong);
  background: var(--chrome);
  border-radius: 0 var(--r-md) var(--r-md) 0;
}

.comment {
  display: grid;
  gap: 2px;
}

.comment__meta {
  font-size: 0.625rem;
}

.comment__note {
  font-size: var(--t-sm);
  color: var(--ink-2);
  line-height: 1.45;
  margin: 0;
}

.seg__findings {
  display: grid;
  gap: 4px;
  padding: var(--s2) var(--s3);
  border-radius: var(--r-md);
  background: var(--chrome);
}

.finding {
  font-size: var(--t-xs);
  color: var(--ink-2);
  display: flex;
  gap: 6px;
  align-items: baseline;
}

.seg__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s3);
  flex-wrap: wrap;
  padding-top: var(--s2);
  border-top: 1px solid var(--rule);
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
  transition:
    background var(--fast),
    border-color var(--fast),
    color var(--fast);
}

.cite:hover {
  border-color: var(--mark);
  color: var(--mark-deep);
}

.cite--on {
  background: var(--mark);
  border-color: var(--mark);
  color: #fff;
}

.cite--broken {
  border-color: var(--fail);
  color: var(--fail);
}

.cite__n {
  font-size: 0.5625rem;
}

.cite__range {
  opacity: 0.7;
}

.acts {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

/* --------------------------------------------------------------- dialog */

.quote {
  padding: var(--s3);
  border-radius: var(--r-md);
  background: var(--chrome);
  font-size: var(--t-sm);
  max-height: 220px;
  overflow: auto;
}

.summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--s4);
  margin: 0;
}

.summary dd {
  margin: 2px 0 0;
  font-size: var(--t-lg);
}

</style>
