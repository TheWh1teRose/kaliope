<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import StatusPill from '@/components/StatusPill.vue'
import { t } from '@/i18n'
import { useRunsStore } from '@/stores/runs'

const runs = useRunsStore()

function when(value: string): string {
  return new Date(value).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })
}

onMounted(() => runs.load())
</script>

<template>
  <div class="page">
    <header>
      <p class="eyebrow">{{ t.app.name }}</p>
      <h1 class="h-page">{{ t.nav.runs }}</h1>
    </header>

    <p v-if="!runs.items.length" class="muted empty">{{ t.run.empty }}</p>

    <table v-else class="runs">
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
        <tr v-for="run in runs.items" :key="run.id">
          <td>
            <RouterLink :to="{ name: 'run', params: { id: run.id } }" class="link">
              {{ run.document_title || run.document_id }}
            </RouterLink>
            <span class="meta block">{{ when(run.created_at) }}</span>
          </td>
          <td class="meta">{{ run.flow_id }} v{{ run.flow_version }}</td>
          <td><StatusPill :status="run.status" /></td>
          <td class="right num">${{ run.total_cost_usd.toFixed(4) }}</td>
          <td class="right num">{{ run.target_minutes ?? t.common.none }}</td>
          <td class="right">
            <RouterLink
              v-if="['completed', 'in_review', 'reviewed'].includes(run.status)"
              class="btn btn--sm"
              :to="{ name: 'review', params: { id: run.id } }"
            >
              {{ t.review.title }}
            </RouterLink>
          </td>
        </tr>
      </tbody>
    </table>
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

.empty {
  padding: var(--s8);
  text-align: center;
}
</style>
