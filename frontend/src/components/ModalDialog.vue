<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{ open: boolean; title: string; lead?: string; wide?: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const panel = ref<HTMLElement | null>(null)

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    await nextTick()
    panel.value?.querySelector<HTMLElement>('[data-autofocus]')?.focus()
  },
)
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="scrim"
      role="dialog"
      aria-modal="true"
      :aria-label="title"
      @click.self="emit('close')"
      @keydown.esc="emit('close')"
    >
      <div ref="panel" class="panel" :class="{ 'panel--wide': wide }" tabindex="-1">
        <header class="panel__head">
          <div>
            <h2 class="h-section">{{ title }}</h2>
            <p v-if="lead" class="panel__lead muted">{{ lead }}</p>
          </div>
          <button class="btn btn--ghost btn--sm" :aria-label="t.common.close" @click="emit('close')">
            ✕
          </button>
        </header>
        <div class="panel__body scroll"><slot /></div>
        <footer v-if="$slots.actions" class="panel__foot"><slot name="actions" /></footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: var(--s5);
  background: rgb(15 21 35 / 0.44);
  backdrop-filter: blur(2px);
  animation: fade var(--fast) both;
}

.panel {
  width: min(560px, 100%);
  max-height: min(86vh, 780px);
  display: flex;
  flex-direction: column;
  background: var(--card);
  border: 1px solid var(--rule-strong);
  border-radius: var(--r-lg);
  box-shadow: var(--shadow-lg);
  animation: rise var(--slow) both;
}

.panel--wide {
  width: min(820px, 100%);
}

.panel__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--s4);
  padding: var(--s5) var(--s5) var(--s4);
  border-bottom: 1px solid var(--rule);
}

.panel__lead {
  margin-top: 4px;
  font-size: var(--t-sm);
  max-width: 52ch;
}

.panel__body {
  padding: var(--s5);
  overflow: auto;
}

.panel__foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--s3);
  padding: var(--s4) var(--s5);
  border-top: 1px solid var(--rule);
  background: var(--chrome);
  border-radius: 0 0 var(--r-lg) var(--r-lg);
}

@keyframes fade {
  from {
    opacity: 0;
  }
}

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(10px) scale(0.985);
  }
}
</style>
