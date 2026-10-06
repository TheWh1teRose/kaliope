<script setup lang="ts">
import { ref, watch } from 'vue'
import type { SpeechVoice } from '@/api/types'
const props = defineProps<{ voice?: SpeechVoice }>()
const state = ref('')
watch(() => props.voice?.voice_id, () => { state.value = '' })
</script>
<template>
  <div class="preview">
    <template v-if="voice?.preview_url">
      <span class="meta">Stimmprobe · {{ voice.name }} (ElevenLabs)</span>
      <audio :key="voice.voice_id" controls preload="none" :src="voice.preview_url"
        :aria-label="`Stimmprobe ${voice.name}`" @loadstart="state = 'Stimmprobe wird geladen …'"
        @canplay="state = ''" @error="state = 'Stimmprobe konnte nicht geladen werden.'" />
      <p v-if="state" role="status" class="muted">{{ state }}</p>
    </template>
    <p v-else class="muted">{{ voice ? 'Für diese Stimme ist keine Stimmprobe verfügbar.' : 'Stimme auswählen, um eine Stimmprobe anzuhören.' }}</p>
  </div>
</template>
<style scoped>
.preview { display: grid; gap: var(--s2); }
audio { width: 100%; }
</style>
