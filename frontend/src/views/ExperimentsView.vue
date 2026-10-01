<script setup lang="ts">
/**
 * The experiment collection.
 *
 * Every experiment is coded on its own and shows up here as one card, with how
 * much it has been used. Opening a card opens that experiment's page.
 */
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { ApiError } from '@/api/client'
import type { ExperimentSummary } from '@/api/types'
import { experimentViews } from '@/experiments'
import { listExperiments } from '@/experiments/api'
import { t } from '@/i18n'

const items = ref<ExperimentSummary[]>([])
const loading = ref(true)
const error = ref('')

function when(value: string | null): string {
  if (!value) return t.experiments.never
  return new Date(value).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}

onMounted(async () => {
  try {
    items.value = await listExperiments()
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="page">
    <header class="head">
      <p class="eyebrow">{{ t.nav.experimenting }}</p>
      <h1 class="h-page">{{ t.experiments.title }}</h1>
      <p class="muted lead">{{ t.experiments.lead }}</p>
    </header>

    <div class="grid">
      <p v-if="error" class="banner">{{ error }}</p>
      <p v-if="loading" class="muted">{{ t.common.loading }}</p>
      <template v-else>
        <component
          :is="experimentViews[item.key] ? RouterLink : 'div'"
          v-for="item in items"
          :key="item.key"
          class="xcard"
          :class="{ 'xcard--closed': !experimentViews[item.key] }"
          :to="{ name: 'experiment', params: { key: item.key } }"
        >
          <div class="spread">
            <span class="eyebrow">{{ item.target }}</span>
            <span class="badge badge--mark">v{{ item.version }}</span>
          </div>
          <h2 class="h-section">{{ item.title }}</h2>
          <p class="small">{{ item.summary }}</p>
          <dl class="stats">
            <div>
              <dt>{{ t.experiments.collected }}</dt>
              <dd class="num">{{ item.stats.saved_count }}</dd>
            </div>
            <div>
              <dt>{{ t.experiments.lastRun }}</dt>
              <dd class="num">{{ when(item.stats.last_run_at) }}</dd>
            </div>
            <div>
              <dt>{{ t.experiments.cost }}</dt>
              <dd class="num">${{ item.stats.spent_usd.toFixed(4) }}</dd>
            </div>
          </dl>
          <span v-if="experimentViews[item.key]" class="btn btn--sm open">
            {{ t.experiments.open }} →
          </span>
          <span v-else class="muted small">{{ t.experiments.noView }}</span>
        </component>
        <div class="empty">
          <p class="eyebrow">{{ items.length ? t.experiments.moreEyebrow : t.experiments.emptyEyebrow }}</p>
          <h2 class="h-section muted">
            {{ items.length ? t.experiments.moreTitle : t.experiments.emptyTitle }}
          </h2>
          <p class="muted small">{{ t.experiments.emptyLead }}</p>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  grid-template-rows: auto 1fr;
  height: 100%;
  min-height: 0;
}

.head {
  padding: var(--s5) var(--s6) var(--s4);
  border-bottom: 1px solid var(--rule);
}

.lead {
  max-width: 66ch;
  margin-top: var(--s2);
  font-size: var(--t-sm);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  align-content: start;
  gap: var(--s4);
  padding: var(--s5) var(--s6);
  overflow: auto;
}

.banner {
  grid-column: 1 / -1;
  padding: var(--s3) var(--s4);
  background: var(--fail-soft);
  color: var(--fail);
  border-radius: var(--r-md);
}

.xcard {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  padding: var(--s5);
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-sm);
  color: inherit;
  text-decoration: none;
  transition:
    border-color var(--fast),
    box-shadow var(--fast);
}

a.xcard:hover {
  border-color: var(--mark);
  box-shadow: var(--shadow-md);
}

.xcard--closed {
  opacity: 0.75;
}

.small {
  font-size: var(--t-sm);
  color: var(--ink-2);
}

.stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--s2);
  padding-top: var(--s3);
  border-top: 1px solid var(--rule);
}

.stats dt {
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.stats dd {
  font-size: var(--t-sm);
}

.open {
  align-self: flex-start;
}

.empty {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  padding: var(--s5);
  border: 1px dashed var(--rule-strong);
  border-radius: var(--r-lg);
}

@media (max-width: 920px) {
  .head,
  .grid {
    padding: var(--s4);
  }
}
</style>
