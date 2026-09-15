<script setup lang="ts">
/**
 * What a node does, as the node itself describes it.
 *
 * The text comes from the node class through `/api/nodes`, so a step whose
 * behaviour changes explains itself differently here with no edit in the
 * console. Someone assembling a pipeline should not have to read Python to know
 * what a step will do to their document.
 */
import { computed } from 'vue'

import type { NodeSpecOut } from '@/api/types'
import { t } from '@/i18n'

const props = defineProps<{ node: NodeSpecOut; compact?: boolean }>()

const inputs = computed(() => Object.entries(props.node.doc.inputs))
</script>

<template>
  <div class="doc">
    <p class="doc__summary prose">{{ node.doc.summary }}</p>

    <section v-if="node.doc.detail.length" class="doc__block">
      <p class="eyebrow">{{ t.pipelines.howItWorks }}</p>
      <ol class="steps">
        <li v-for="(paragraph, index) in node.doc.detail" :key="index" class="prose">
          {{ paragraph }}
        </li>
      </ol>
    </section>

    <div class="doc__grid">
      <section v-if="inputs.length" class="doc__block">
        <p class="eyebrow">{{ t.pipelines.inputsDoc }}</p>
        <ul class="kv">
          <li v-for="[key, text] in inputs" :key="key">
            <span class="kv__key num">{{ key }}</span>
            <span class="kv__text">{{ text }}</span>
          </li>
        </ul>
      </section>

      <section v-if="node.doc.output" class="doc__block">
        <p class="eyebrow">{{ t.pipelines.outputDoc }}</p>
        <ul class="kv">
          <li>
            <span class="kv__key num">{{ node.produces }}</span>
            <span class="kv__text">{{ node.doc.output }}</span>
          </li>
        </ul>
      </section>
    </div>

    <section v-if="!compact && node.doc.failure_modes.length" class="doc__block">
      <p class="eyebrow">{{ t.pipelines.failureModes }}</p>
      <ul class="fails">
        <li v-for="(mode, index) in node.doc.failure_modes" :key="index" class="prose">
          {{ mode }}
        </li>
      </ul>
    </section>

    <p v-if="!compact && node.doc.cost" class="doc__cost">
      <span class="eyebrow">{{ t.pipelines.costNote }}</span>
      <span class="prose">{{ node.doc.cost }}</span>
    </p>

    <p v-if="node.checked_by.length" class="doc__gates meta">
      {{ t.nodeCatalogue.checkedBy }}:
      <span v-for="id in node.checked_by" :key="id" class="badge badge--idle">{{ id }}</span>
    </p>
  </div>
</template>

<style scoped>
.doc {
  display: grid;
  gap: var(--s4);
}

.doc__summary {
  font-size: var(--t-md);
  color: var(--ink);
  margin: 0;
  max-width: 82ch;
}

.doc__block {
  display: grid;
  gap: var(--s2);
}

.doc__grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--s4);
  align-items: start;
}

.steps {
  margin: 0;
  padding-left: 1.2em;
  display: grid;
  gap: var(--s2);
}

.steps li {
  font-size: var(--t-sm);
  color: var(--ink-2);
  line-height: 1.55;
  max-width: 84ch;
}

.steps li::marker {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-4);
}

.kv {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s1);
}

.kv li {
  display: grid;
  gap: 2px;
  padding: var(--s2) var(--s3);
  background: var(--chrome);
  border-radius: var(--r-sm);
}

.kv__key {
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--mark-deep);
}

.kv__text {
  font-size: var(--t-sm);
  color: var(--ink-2);
  line-height: 1.5;
}

.fails {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s2);
}

.fails li {
  position: relative;
  padding: var(--s2) var(--s3) var(--s2) var(--s5);
  border-radius: var(--r-sm);
  background: var(--warn-soft);
  color: var(--ink-2);
  font-size: var(--t-sm);
  line-height: 1.5;
}

.fails li::before {
  content: '!';
  position: absolute;
  left: var(--s3);
  font-family: var(--mono);
  color: var(--warn);
}

.doc__cost {
  margin: 0;
  display: grid;
  gap: 2px;
}

.doc__cost .prose {
  font-size: var(--t-sm);
  color: var(--ink-2);
  margin: 0;
}

.doc__gates {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: 0;
}

@media (max-width: 820px) {
  .doc__grid {
    grid-template-columns: 1fr;
  }
}
</style>
