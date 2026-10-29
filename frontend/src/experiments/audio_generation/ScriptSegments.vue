<script setup lang="ts">
/**
 * The prepared script, segment by segment: tick what is spoken (a click with
 * Shift ticks a whole stretch), or pick a range; edit a segment's text while
 * its speaker stays, and get the original back with one click.
 */
import { computed, nextTick, ref } from 'vue'

import { fill, t } from '@/i18n'

import {
  choose,
  firstMinuteIds,
  isChosen,
  isEdited,
  pieces,
  rangeIds,
  textOf,
  type Line,
  type Setup,
} from './synthesis'

const props = defineProps<{ lines: Line[]; setup: Setup }>()
const emit = defineEmits<{
  (e: 'select', ids: string[] | null): void
  (e: 'edit', id: string, text: string | null): void
}>()

const LONG = 360

const labels = t.experiments.audioGeneration
const editing = ref<string | null>(null)
const from = ref(1)
const to = ref(Math.min(props.lines.length, 5))
const anchor = ref<number | null>(null)
/** Long segments read in full only on request. */
const open = ref(new Set<string>())
const speakers = computed(() => [...new Set(props.lines.map((line) => line.speaker))])
const anyEdited = computed(() => props.lines.some((line) => isEdited(line, props.setup)))
const chosenIds = computed(
  () => new Set(props.lines.filter((line) => isChosen(line, props.setup)).map((l) => l.segment_id)),
)

function speakerClass(speaker: string): string {
  return `who--${Math.min(speakers.value.indexOf(speaker), 3)}`
}

function long(line: Line): boolean {
  return textOf(line, props.setup).length > LONG
}

function toggle(id: string): void {
  const next = new Set(open.value)
  if (!next.delete(id)) next.add(id)
  open.value = next
}

function select(ids: Iterable<string>): void {
  emit('select', choose(props.lines, ids))
}

function tick(index: number, event: MouseEvent): void {
  const line = props.lines[index]
  if (!line) return
  const next = new Set(chosenIds.value)
  const on = !next.has(line.segment_id)
  const stretch =
    event.shiftKey && anchor.value !== null
      ? rangeIds(props.lines, anchor.value + 1, index + 1)
      : [line.segment_id]
  for (const id of stretch) {
    if (on) next.add(id)
    else next.delete(id)
  }
  anchor.value = index
  select(next)
}

function applyRange(): void {
  select(rangeIds(props.lines, from.value || 1, to.value || props.lines.length))
}

async function startEdit(line: Line): Promise<void> {
  editing.value = line.segment_id
  await nextTick()
  document.querySelector<HTMLTextAreaElement>(`[data-edit="${line.segment_id}"]`)?.focus()
}

function input(line: Line, value: string): void {
  emit('edit', line.segment_id, value === line.tagged ? null : value)
}

function restoreAll(): void {
  if (!window.confirm(labels.restoreAllConfirm)) return
  editing.value = null
  for (const line of props.lines) if (isEdited(line, props.setup)) emit('edit', line.segment_id, null)
}
</script>

<template>
  <div class="tools">
    <div class="tools__row">
      <span class="eyebrow">{{ labels.choose }}</span>
      <div class="row wrap">
        <button class="btn btn--sm" data-select="all" @click="emit('select', null)">{{ labels.all }}</button>
        <button class="btn btn--sm" data-select="none" @click="select([])">{{ labels.none }}</button>
        <button class="btn btn--sm" data-select="minute" @click="select(firstMinuteIds(lines, setup))">
          {{ labels.firstMinute }}
        </button>
      </div>
    </div>
    <div class="tools__row">
      <label class="num-field">
        <span class="meta">{{ labels.rangeFrom }}</span>
        <input v-model.number="from" class="input" type="number" min="1" :max="lines.length" data-range="from" />
      </label>
      <label class="num-field">
        <span class="meta">{{ labels.rangeTo }}</span>
        <input v-model.number="to" class="input" type="number" min="1" :max="lines.length" data-range="to" />
      </label>
      <button class="btn btn--sm" data-range="apply" @click="applyRange">{{ labels.applyRange }}</button>
      <span class="grow" />
      <button v-if="anyEdited" class="btn btn--ghost btn--sm" data-restore-all @click="restoreAll">
        {{ labels.restoreAll }}
      </button>
    </div>
    <p class="hint">{{ labels.shiftHint }} {{ labels.editHint }}</p>
  </div>

  <ol class="segments">
    <li
      v-for="(line, index) in lines"
      :key="line.segment_id"
      class="seg"
      :class="[speakerClass(line.speaker), { 'seg--off': !chosenIds.has(line.segment_id) }]"
      :data-segment="line.segment_id"
    >
      <div class="seg__head">
        <input
          type="checkbox"
          class="seg__tick"
          :checked="chosenIds.has(line.segment_id)"
          :aria-label="fill(labels.include, { n: index + 1 })"
          @click="tick(index, $event)"
        />
        <span class="seg__n num">{{ index + 1 }}</span>
        <span class="who">{{ line.speaker }}</span>
        <span v-if="isEdited(line, setup)" class="badge badge--warn">{{ labels.edited }}</span>
        <span class="grow" />
        <span class="meta num">{{ fill(labels.characters, { n: textOf(line, setup).length }) }}</span>
        <button
          v-if="isEdited(line, setup)"
          class="btn btn--ghost btn--sm"
          data-restore
          @click="emit('edit', line.segment_id, null)"
        >
          {{ labels.restore }}
        </button>
        <button
          v-if="editing !== line.segment_id"
          class="btn btn--ghost btn--sm"
          data-edit-start
          @click="startEdit(line)"
        >
          {{ labels.edit }}
        </button>
        <button v-else class="btn btn--ghost btn--sm" @click="editing = null">{{ labels.editDone }}</button>
      </div>
      <textarea
        v-if="editing === line.segment_id"
        class="textarea seg__edit"
        :data-edit="line.segment_id"
        :rows="Math.max(3, Math.ceil(textOf(line, setup).length / 70))"
        :value="textOf(line, setup)"
        :aria-label="fill(labels.editLabel, { n: index + 1 })"
        spellcheck="false"
        @input="input(line, ($event.target as HTMLTextAreaElement).value)"
        @keydown.esc="editing = null"
      />
      <template v-else>
        <p
          class="seg__text"
          :class="{ 'seg__text--clamped': long(line) && !open.has(line.segment_id) }"
          @dblclick="startEdit(line)"
        >
          <template v-for="(piece, pi) in pieces(textOf(line, setup))" :key="pi">
            <span v-if="piece.tag" class="tag">{{ piece.text }}</span>
            <template v-else>{{ piece.text }}</template>
          </template>
        </p>
        <button v-if="long(line)" class="more" @click="toggle(line.segment_id)">
          {{ open.has(line.segment_id) ? labels.less : labels.more }}
        </button>
      </template>
      <p v-if="!textOf(line, setup).trim()" class="warnline">{{ labels.emptyText }}</p>
    </li>
  </ol>
</template>

<style scoped>
.tools {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  padding: var(--s3) var(--s4);
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
}

.tools__row {
  display: flex;
  align-items: flex-end;
  gap: var(--s2);
  flex-wrap: wrap;
}

.tools__row .eyebrow {
  align-self: center;
  margin-right: var(--s2);
}

.num-field {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.num-field .input {
  width: 6.5rem;
  padding: 4px 8px;
  font-family: var(--mono);
  font-size: var(--t-sm);
}

.hint {
  margin: 0;
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.segments {
  display: flex;
  flex-direction: column;
  gap: var(--s2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.seg {
  --who: var(--ink-3);
  padding: var(--s2) var(--s3) var(--s3);
  background: var(--card);
  border: 1px solid var(--rule);
  border-left: 3px solid var(--who);
  border-radius: var(--r-lg);
  transition: opacity 0.15s;
}

.who--0 {
  --who: var(--zone-objective);
}

.who--1 {
  --who: var(--zone-emphasis_callout);
}

.who--2 {
  --who: var(--zone-exercise);
}

.seg--off {
  opacity: 0.5;
  background: var(--chrome);
}

.seg__head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  min-width: 0;
}

.seg__tick {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--mark);
  cursor: pointer;
}

.seg__n {
  min-width: 1.6em;
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.who {
  padding: 1px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--who) 12%, var(--card));
  color: var(--who);
  font-size: var(--t-xs);
  font-weight: 620;
}

.seg__text {
  margin: 6px 0 0 calc(16px + 1.6em + 2 * var(--s2));
  font-family: var(--serif);
  font-size: var(--t-md);
  line-height: 1.55;
  white-space: pre-wrap;
  color: var(--ink);
}

.seg__text--clamped {
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.more {
  margin: 2px 0 0 calc(16px + 1.6em + 2 * var(--s2));
  padding: 0;
  border: 0;
  background: none;
  color: var(--mark);
  font-size: var(--t-xs);
  cursor: pointer;
}

.seg__edit {
  margin-top: 6px;
  font-family: var(--serif);
  font-size: var(--t-md);
  line-height: 1.55;
}

.tag {
  padding: 0 4px;
  border-radius: 4px;
  background: var(--sunk);
  color: var(--ink-2);
  font-family: var(--mono);
  font-size: 0.78em;
}

.warnline {
  margin: var(--s2) 0 0;
  padding: 4px 10px;
  border-radius: var(--r-md);
  background: var(--warn-soft);
  color: var(--warn);
  font-size: var(--t-xs);
}
</style>
