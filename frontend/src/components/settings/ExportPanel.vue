<script setup lang="ts">
import { t } from '@/i18n'
</script>

<template>
  <section class="stack" aria-labelledby="export-title">
    <header>
      <h2 id="export-title" class="h-section">{{ t.exports.title }}</h2>
      <p class="muted lead">{{ t.exports.lead }}</p>
    </header>

    <div class="sheet export">
      <div class="grow">
        <h3 class="h-section">{{ t.exports.editEvents }}</h3>
        <p class="muted hint">{{ t.exports.editEventsHint }}</p>
        <code class="path">GET /api/exports/edit-events.jsonl</code>
      </div>
      <a class="btn btn--mark" href="/api/exports/edit-events.jsonl" download>
        {{ t.exports.download }}
      </a>
    </div>
  </section>
</template>

<style scoped>
.lead {
  margin-top: 6px;
  font-size: var(--t-sm);
}

.export {
  display: flex;
  align-items: center;
  gap: var(--s5);
}

.export .btn {
  text-decoration: none;
}

.hint {
  font-size: var(--t-sm);
  margin: 4px 0 var(--s3);
}

.path {
  font-family: var(--mono);
  font-size: var(--t-xs);
  padding: 3px 7px;
  border-radius: var(--r-sm);
  background: var(--sunk);
  color: var(--ink-2);
}
</style>
