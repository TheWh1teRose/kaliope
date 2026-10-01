<script setup lang="ts">
/**
 * The page frame every experiment uses: header with spend and actions, then
 * two columns, "Aufbau" (setup) on the left and "Ausgaben" (outputs) on the
 * right. Experiments fill the slots; the frame keeps them looking the same.
 */
import { RouterLink } from 'vue-router'

import { t } from '@/i18n'

defineProps<{
  experimentKey: string
  title: string
  lead?: string
  spentUsd?: number
  runCount?: number
}>()
</script>

<template>
  <div class="frame">
    <header class="head">
      <div class="grow">
        <p class="eyebrow crumb">
          <RouterLink :to="{ name: 'experiments' }">{{ t.experiments.title }}</RouterLink>
          <span aria-hidden="true">/</span>
          <span>{{ experimentKey }}</span>
        </p>
        <h1 class="h-page">{{ title }}</h1>
        <p v-if="lead" class="muted lead">{{ lead }}</p>
      </div>
      <div class="row wrap tools">
        <span v-if="spentUsd !== undefined" class="meta num">
          {{ t.experiments.spent }} ${{ spentUsd.toFixed(4) }}
          <template v-if="runCount !== undefined"> · {{ runCount }} {{ t.experiments.runs }}</template>
        </span>
        <slot name="actions" />
      </div>
    </header>
    <slot name="banner" />
    <div class="work">
      <aside class="col col--setup scroll" :aria-label="t.experiments.setup">
        <slot name="setup" />
      </aside>
      <section class="col scroll" :aria-label="t.experiments.outputs">
        <slot name="outputs" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.frame {
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr);
  height: 100%;
  min-height: 0;
}

.head {
  display: flex;
  align-items: flex-end;
  gap: var(--s5);
  flex-wrap: wrap;
  padding: var(--s5) var(--s6) var(--s4);
  border-bottom: 1px solid var(--rule);
}

.crumb {
  display: flex;
  gap: 6px;
  align-items: center;
}

.crumb a {
  color: var(--ink-3);
  text-decoration: none;
}

.crumb a:hover {
  color: var(--mark);
}

.lead {
  max-width: 66ch;
  margin-top: var(--s2);
  font-size: var(--t-sm);
}

.tools {
  padding-bottom: 2px;
}

.work {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.12fr);
  min-height: 0;
}

.col {
  min-height: 0;
  padding: var(--s4) var(--s5) var(--s6);
}

.col--setup {
  background: var(--chrome);
  border-right: 1px solid var(--rule);
}

@media (max-width: 920px) {
  .frame {
    height: auto;
  }

  .work {
    grid-template-columns: minmax(0, 1fr);
  }

  .col--setup {
    border-right: 0;
    border-bottom: 1px solid var(--rule);
  }

  .head {
    padding: var(--s4);
  }
}
</style>
