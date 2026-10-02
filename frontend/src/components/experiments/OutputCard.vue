<script setup lang="ts">
/**
 * One model answer as a card: title, badges, facts, warnings, the answer as
 * Text or JSON, what the model was sent, and actions in the footer slot.
 * Used for the current output and for every collected output. The `text`
 * slot replaces only the Text view; `body` replaces both views.
 */
import { computed, ref, watch } from 'vue'

import type { ExperimentCall } from '@/api/types'
import OutputText from '@/components/experiments/OutputText.vue'
import { t } from '@/i18n'

export type OutputView = 'text' | 'json'

const props = defineProps<{
  title: string
  /** The parsed answer, or null for plain text. */
  payload: unknown
  /** The raw answer text. */
  text?: string | null
  badges?: { text: string; mark?: boolean }[]
  facts?: string[]
  warnings?: string[]
  calls?: ExperimentCall[]
  current?: boolean
  collapsible?: boolean
  /** Set from outside to switch every card at once. */
  view?: OutputView
}>()

const mode = ref<OutputView>(props.view ?? 'text')
watch(
  () => props.view,
  (next) => {
    if (next) mode.value = next
  },
)
const open = ref(!props.collapsible)
const promptOpen = ref(false)
const jsonText = computed(() =>
  props.payload === null || props.payload === undefined
    ? (props.text ?? '')
    : JSON.stringify(props.payload, null, 2),
)
</script>

<template>
  <article class="out" :class="{ 'out--current': current, 'out--collapsed': !open }">
    <div class="out__head">
      <span class="out__title grow truncate" :title="title">{{ title }}</span>
      <slot name="head" />
      <span class="seg" role="group">
        <button :aria-pressed="mode === 'text'" @click="mode = 'text'">{{ t.experiments.text }}</button>
        <button :aria-pressed="mode === 'json'" @click="mode = 'json'">{{ t.experiments.json }}</button>
      </span>
    </div>
    <div v-if="badges?.length" class="out__badges">
      <span v-for="b in badges" :key="b.text" class="badge" :class="b.mark ? 'badge--mark' : 'badge--idle'">
        {{ b.text }}
      </span>
    </div>
    <div v-if="facts?.length" class="out__facts">
      <span v-for="fact in facts" :key="fact" class="meta num">{{ fact }}</span>
    </div>
    <p v-for="warning in warnings ?? []" :key="warning" class="warnline">{{ warning }}</p>
    <div class="out__body">
      <slot name="body" :mode="mode">
        <slot v-if="mode === 'text'" name="text">
          <OutputText :payload="payload" :text="text" />
        </slot>
        <pre v-else class="json">{{ jsonText }}</pre>
      </slot>
    </div>
    <template v-if="calls?.length">
      <button class="fold" :aria-expanded="promptOpen" @click="promptOpen = !promptOpen">
        <span class="eyebrow">{{ t.experiments.whatModelGot }}</span>
        <span class="grow" />
        <span aria-hidden="true">{{ promptOpen ? '−' : '+' }}</span>
      </button>
      <div v-if="promptOpen" class="foldbody">
        <template v-for="(call, ci) in calls" :key="ci">
          <p class="meta">{{ call.model }}</p>
          <template v-if="call.system">
            <p class="role">{{ t.experiments.system }}</p>
            <pre class="json">{{ call.system }}</pre>
          </template>
          <template v-for="(message, mi) in call.messages" :key="mi">
            <p class="role">{{ t.experiments.user }}</p>
            <pre class="json">{{ message.content }}</pre>
          </template>
        </template>
      </div>
    </template>
    <div v-if="$slots.foot || collapsible" class="out__foot">
      <button v-if="collapsible" class="btn btn--ghost btn--sm" @click="open = !open">
        {{ open ? t.experiments.collapse : t.experiments.readAll }}
      </button>
      <slot name="foot" />
    </div>
  </article>
</template>

<style scoped>
.out {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-sm);
}

.out--current {
  border-color: var(--mark);
  box-shadow:
    0 0 0 3px var(--mark-soft),
    var(--shadow-sm);
}

.out__head,
.out__badges,
.out__facts {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  padding: var(--s3) var(--s4) 0;
}

.out__badges,
.out__facts {
  padding-top: 6px;
}

.out__title {
  font-weight: 620;
}

.out__body {
  padding: var(--s3) var(--s4);
}

.out--collapsed .out__body {
  max-height: 190px;
  overflow: hidden;
  -webkit-mask-image: linear-gradient(#000 65%, transparent);
  mask-image: linear-gradient(#000 65%, transparent);
}

.out__foot {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  padding: var(--s2) var(--s4) var(--s3);
  border-top: 1px solid var(--rule);
}

.warnline {
  margin: var(--s2) var(--s4) 0;
  padding: 6px 10px;
  border-radius: var(--r-md);
  background: var(--warn-soft);
  color: var(--warn);
  font-size: var(--t-xs);
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

.fold {
  display: flex;
  align-items: center;
  gap: var(--s2);
  width: 100%;
  padding: var(--s2) var(--s4);
  border: 0;
  border-top: 1px solid var(--rule);
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.foldbody {
  padding: 0 var(--s4) var(--s3);
}

.role {
  margin: var(--s2) 0 4px;
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.json {
  margin: 0;
  max-height: 420px;
  overflow: auto;
  padding: var(--s3);
  background: var(--sunk);
  border-radius: var(--r-md);
  font-family: var(--mono);
  font-size: 0.74rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
