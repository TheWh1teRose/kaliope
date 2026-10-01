<script setup lang="ts">
/**
 * The experiment collection.
 *
 * Every experiment is coded on its own and shows up here as one card. Until
 * the first one exists, the page only explains where experiments come from.
 */
import { t } from '@/i18n'
</script>

<template>
  <div class="page">
    <header class="head">
      <p class="eyebrow">{{ t.nav.experimenting }}</p>
      <h1 class="h-page">{{ t.experiments.title }}</h1>
      <p class="muted lead">{{ t.experiments.lead }}</p>
    </header>

    <div class="grid">
      <div class="empty">
        <p class="eyebrow">{{ t.experiments.emptyEyebrow }}</p>
        <h2 class="h-section muted">{{ t.experiments.emptyTitle }}</h2>
        <p class="muted small">{{ t.experiments.emptyLead }}</p>
      </div>
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

.empty {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  padding: var(--s5);
  border: 1px dashed var(--rule-strong);
  border-radius: var(--r-lg);
}

.small {
  font-size: var(--t-sm);
}
</style>
