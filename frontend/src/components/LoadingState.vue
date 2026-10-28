<script setup lang="ts">
/**
 * What a page or panel shows while its data is on the way: a small spinner and
 * a line of text, announced to screen readers. Pass a class for the spacing the
 * spot needs; without `label` it reads the shared "loading" message.
 */
import { t } from '@/i18n'

defineProps<{ label?: string }>()
</script>

<template>
  <p class="loading" role="status" data-loading>
    <span class="loading__spinner" aria-hidden="true" />
    <span>{{ label ?? t.common.loading }}</span>
  </p>
</template>

<style scoped>
.loading {
  display: flex;
  align-items: center;
  gap: var(--s2);
  margin: 0;
  font-size: var(--t-sm);
  color: var(--ink-3);
}

.loading__spinner {
  flex: none;
  width: 14px;
  height: 14px;
  border: 2px solid var(--rule-strong);
  border-top-color: var(--mark);
  border-radius: 50%;
  animation: loading-spin 0.8s linear infinite;
}

@keyframes loading-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
