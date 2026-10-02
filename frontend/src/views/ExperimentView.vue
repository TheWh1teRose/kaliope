<script setup lang="ts">
/** Opens one experiment by key; each experiment brings its own page. */
import { computed, defineAsyncComponent } from 'vue'

import { experimentViews } from '@/experiments'
import { t } from '@/i18n'

const props = defineProps<{ experimentKey: string }>()

const view = computed(() => {
  const load = experimentViews[props.experimentKey]
  return load ? defineAsyncComponent(load) : null
})
</script>

<template>
  <component :is="view" v-if="view" />
  <div v-else class="missing">
    <p class="eyebrow">{{ t.nav.experimenting }}</p>
    <p class="muted">{{ t.experiments.unknown }}</p>
    <RouterLink :to="{ name: 'experiments' }">{{ t.experiments.title }}</RouterLink>
  </div>
</template>

<style scoped>
.missing {
  display: flex;
  flex-direction: column;
  gap: var(--s3);
  padding: var(--s6);
}
</style>
