<script setup lang="ts">
import FeedbackBoard from '@/components/FeedbackBoard.vue'
import { t } from '@/i18n'
</script>

<template>
  <div class="page">
    <header>
      <p class="eyebrow">{{ t.app.name }}</p>
      <h1 class="h-page">{{ t.nav.feedback }}</h1>
    </header>
    <FeedbackBoard />
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
</style>
