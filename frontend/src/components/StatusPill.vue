<script setup lang="ts">
import { computed } from 'vue'

import type { RunStatus } from '@/api/types'
import { t } from '@/i18n'

const props = defineProps<{ status: RunStatus | string; kind?: 'run' | 'parse' | 'gate' | 'node' }>()

const tone = computed(() => {
  switch (props.status) {
    case 'completed':
    case 'reviewed':
    case 'parsed':
    case 'pass':
    case 'ok':
    case 'cached':
      return 'pass'
    case 'failed':
    case 'fail':
    case 'blocked':
      return 'fail'
    case 'warn':
      return 'warn'
    case 'running':
    case 'parsing':
    case 'in_review':
    case 'paused':
      return 'mark'
    default:
      return 'idle'
  }
})

const label = computed(() => {
  const kind = props.kind ?? 'run'
  const table: Record<string, Record<string, string>> = {
    run: t.run.status,
    parse: t.documents.parseStatus,
    gate: t.gate.status,
    node: t.graph.status,
  }
  return table[kind]?.[props.status] ?? props.status
})

const live = computed(() => tone.value === 'mark' && props.status !== 'in_review')
</script>

<template>
  <span class="badge" :class="`badge--${tone}`">
    <span v-if="live" class="pulse" aria-hidden="true" />
    {{ label }}
  </span>
</template>

<style scoped>
.pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  animation: breathe 1.4s ease-in-out infinite;
}

@keyframes breathe {
  0%,
  100% {
    opacity: 0.35;
    transform: scale(0.85);
  }
  50% {
    opacity: 1;
    transform: scale(1.15);
  }
}
</style>
