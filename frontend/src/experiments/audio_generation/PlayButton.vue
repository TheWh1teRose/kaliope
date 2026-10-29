<script setup lang="ts">
/** Round play/pause control of a voice sample, with its loading and failed states. */
import type { SampleState } from './samplePlayer'

defineProps<{ state: SampleState; label: string; busy?: boolean; disabled?: boolean }>()
defineEmits<{ (e: 'click'): void }>()
</script>

<template>
  <button
    class="play"
    :class="`play--${state}`"
    :aria-label="label"
    :aria-pressed="state === 'playing'"
    :disabled="disabled || busy"
    data-play
    @click="$emit('click')"
  >
    <span v-if="busy || state === 'loading'" class="spinner" aria-hidden="true" />
    <svg v-else-if="state === 'playing'" viewBox="0 0 12 12" aria-hidden="true">
      <rect x="2.5" y="2" width="2.5" height="8" rx="0.6" />
      <rect x="7" y="2" width="2.5" height="8" rx="0.6" />
    </svg>
    <svg v-else viewBox="0 0 12 12" aria-hidden="true"><path d="M3.5 2.2v7.6a.5.5 0 0 0 .76.43l6.1-3.8a.5.5 0 0 0 0-.86l-6.1-3.8a.5.5 0 0 0-.76.43z" /></svg>
  </button>
</template>

<style scoped>
.play {
  display: inline-grid;
  flex: none;
  place-items: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 1px solid var(--rule-strong);
  border-radius: 50%;
  background: var(--card);
  color: var(--ink);
  cursor: pointer;
}

.play svg {
  width: 12px;
  height: 12px;
  fill: currentColor;
}

.play:hover:not(:disabled) {
  border-color: var(--mark);
  color: var(--mark);
}

.play--playing,
.play--playing:hover:not(:disabled) {
  border-color: var(--mark);
  background: var(--mark);
  color: #fff;
}

.play--failed {
  border-color: var(--fail);
  color: var(--fail);
}

.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--rule-strong);
  border-top-color: var(--mark);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
