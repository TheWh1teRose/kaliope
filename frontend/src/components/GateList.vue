<script setup lang="ts">
import { ref } from 'vue'

import type { GateReport } from '@/api/types'
import StatusPill from '@/components/StatusPill.vue'
import { t, tt } from '@/i18n'

defineProps<{ gates: GateReport[] }>()

const open = ref<string | null>(null)

function toggle(id: string): void {
  open.value = open.value === id ? null : id
}
</script>

<template>
  <ul class="gates">
    <li v-for="gate in gates" :key="gate.id" class="gate" :class="`gate--${gate.status}`">
      <button class="gate__head" :aria-expanded="open === gate.id" @click="toggle(gate.id)">
        <span class="gate__id num">{{ gate.id }}</span>
        <span class="gate__name grow truncate">{{ tt(`gate.${gate.id}`, gate.name) }}</span>
        <span v-if="gate.violations.length" class="gate__count meta">
          {{ gate.violations.length }}
        </span>
        <StatusPill :status="gate.status" kind="gate" />
      </button>
      <div v-if="open === gate.id" class="gate__body">
        <p v-if="gate.skip_reason" class="muted small">
          {{ t.gate.skipReason }}: {{ gate.skip_reason }}
        </p>
        <p v-else-if="!gate.violations.length" class="muted small">{{ t.gate.noViolations }}</p>
        <ul v-else class="findings">
          <li v-for="(violation, index) in gate.violations" :key="index" class="finding">
            <span v-if="violation.target_id" class="meta">{{ violation.target_id }}</span>
            <span class="small">{{ violation.message }}</span>
          </li>
        </ul>
      </div>
    </li>
  </ul>
</template>

<style scoped>
.gates {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 3px;
}

.gate {
  border: 1px solid var(--rule);
  border-left: 3px solid var(--idle);
  border-radius: var(--r-md);
  background: var(--card);
  overflow: hidden;
}

.gate--pass {
  border-left-color: var(--pass);
}
.gate--warn {
  border-left-color: var(--warn);
}
.gate--fail {
  border-left-color: var(--fail);
}
.gate--skipped {
  border-left-color: var(--rule-strong);
}

.gate__head {
  display: flex;
  align-items: center;
  gap: var(--s3);
  width: 100%;
  padding: 9px 12px;
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.gate__head:hover {
  background: var(--chrome);
}

.gate__id {
  font-size: var(--t-xs);
  color: var(--ink-3);
  width: 22px;
}

.gate__name {
  font-size: var(--t-sm);
  font-weight: 500;
}

.gate__count {
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--sunk);
}

.gate__body {
  padding: 0 12px 12px 40px;
}

.findings {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s2);
}

.finding {
  display: grid;
  gap: 2px;
  padding: var(--s2) var(--s3);
  border-radius: var(--r-sm);
  background: var(--chrome);
}

.small {
  font-size: var(--t-sm);
}
</style>
