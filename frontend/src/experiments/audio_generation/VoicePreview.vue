<script setup lang="ts">
import { ref, watch } from 'vue'
import type { SpeechVoice } from '@/api/types'
import { t } from '@/i18n'
const labels = t.experiments.audioGeneration
const props = defineProps<{ voice?: SpeechVoice }>()
const state = ref('')
watch(() => props.voice?.voice_id, () => { state.value = '' })
</script>
<template>
  <div class="preview">
    <template v-if="voice?.preview_url">
      <span class="meta">{{ labels.preview }} · {{ voice.name }} (ElevenLabs)</span>
      <audio :key="voice.voice_id" controls preload="none" :src="voice.preview_url"
        :aria-label="`${labels.preview} ${voice.name}`" @loadstart="state = labels.previewLoading"
        @canplay="state = ''" @error="state = labels.previewError" />
      <p v-if="state" role="status" class="muted">{{ state }}</p>
    </template>
    <p v-else class="muted">{{ voice ? labels.previewMissing : labels.previewChoose }}</p>
  </div>
</template>
<style scoped>
.preview { display: grid; gap: var(--s2); }
audio { width: 100%; }
</style>
