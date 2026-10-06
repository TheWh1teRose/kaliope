<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { audioApi, takeActive } from '@/api/audio'
import type { AudioTakeOut, ExperimentRun, ExperimentSource, ExperimentSourceMeta, SpeechVoice, VoiceCast } from '@/api/types'
import SourceLoader from '@/components/experiments/SourceLoader.vue'
import { getExperiment, listRuns, startRun, waitForRun } from '@/experiments/api'

const KEY = 'audio_generation'
const DRAFT_KEY = `kalliope-exp:${KEY}`
interface Setup { artifact_hash: string; voice_cast: VoiceCast }
type Draft = Pick<ExperimentRun<Setup>, 'setup' | 'source'>
interface Line { segment_id: string; speaker: string; tagged: string }
const source = ref<ExperimentSourceMeta | null>(null)
const setup = ref<Setup>({ artifact_hash: '', voice_cast: { voices: [], model_id: 'eleven_v4', stability: 0.5, seed: null, language_code: null } })
const lines = ref<Line[]>([])
const voices = ref<SpeechVoice[]>([])
const models = ref<string[]>([])
const runs = ref<ExperimentRun<Setup>[]>([])
const takes = ref<AudioTakeOut[]>([])
const error = ref('')
const busy = ref(false)
const loading = ref(true)
const rates = ref<Record<string, number>>({})
const estimate = computed(() => lines.value.reduce((sum, line) => sum + line.tagged.length, 0) / 1000 * (rates.value[setup.value.voice_cast.model_id] ?? 0))
let gone = false
let timer: ReturnType<typeof setTimeout> | undefined
const speakers = computed(() => [...new Set(lines.value.map(line => line.speaker))])
const valid = computed(() => source.value && lines.value.length && speakers.value.every(speaker => setup.value.voice_cast.voices.some(v => v.speaker === speaker && voices.value.some(voice => voice.voice_id === v.voice_id))))
function fail(exc: unknown) { error.value = exc instanceof Error ? exc.message : String(exc) }
function loaded(value: ExperimentSource) {
  source.value = value.source
  setup.value.artifact_hash = value.fields.artifact_hash ?? ''
  lines.value = (JSON.parse(value.fields.audio_script ?? '{}') as { lines: Line[] }).lines
  setup.value.voice_cast.voices = speakers.value.map(speaker => ({ speaker, voice_id: '' }))
  error.value = ''
}
async function refresh() {
  runs.value = await listRuns<Setup>(KEY)
  const runIds = [...new Set(runs.value.map(run => run.output?.run_id).filter((id): id is string => typeof id === 'string'))]
  const results = await Promise.all(runIds.map(id => audioApi.takes(id)))
  const ids = new Set(runs.value.map(run => run.output?.take_id))
  takes.value = results.flatMap(result => result.takes).filter(take => ids.has(take.id))
}
async function poll() {
  try { await refresh() } catch (exc) { fail(exc) }
  if (!gone) timer = setTimeout(poll, 1500)
}
async function generate() {
  if (!valid.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const run = await startRun(KEY, setup.value, source.value)
    await waitForRun(run.id, () => gone)
    await refresh()
  } catch (exc) { fail(exc) } finally { busy.value = false }
}
watch([setup, source], () => {
  if (loading.value) return
  try { localStorage.setItem(DRAFT_KEY, JSON.stringify({ setup: setup.value, source: source.value })) } catch { /* draft storage is optional */ }
}, { deep: true })
async function restore(run: Draft) {
  setup.value = JSON.parse(JSON.stringify(run.setup)) as Setup
  source.value = run.source
  // Reload the immutable prepared artifact through its source, never from raw text.
  const { loadSource } = await import('@/experiments/api')
  if (run.source) {
    const value = await loadSource(KEY, run.source.run_id, run.source.beat_id)
    lines.value = (JSON.parse(value.fields.audio_script ?? '{}') as { lines: Line[] }).lines
  }
}
async function resume(take: AudioTakeOut) { try { await audioApi.resume(take.id); await refresh() } catch (exc) { fail(exc) } }
async function stop(take: AudioTakeOut) { try { await audioApi.stop(take.id); await refresh() } catch (exc) { fail(exc) } }
onMounted(async () => {
  try {
    const detail = await getExperiment<Setup>(KEY)
    models.value = detail.extras.models as string[]
    const status = await audioApi.status()
    rates.value = status.usd_per_1k_characters ?? {}
    if (status.configured) voices.value = await audioApi.voices()
    else error.value = status.message ?? 'ElevenLabs nicht eingerichtet'
    await refresh()
    let draft: Draft | null = null
    try { draft = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? 'null') as Draft | null } catch { /* ignore malformed drafts */ }
    if (draft?.setup?.artifact_hash && draft.source) await restore(draft)
    else if (runs.value[0]) await restore(runs.value[0])
  } catch (exc) { fail(exc) } finally { loading.value = false; if (!gone) timer = setTimeout(poll, 1500) }
})
onUnmounted(() => { gone = true; clearTimeout(timer) })
</script>

<template>
  <section class="stack">
    <h1>ElevenLabs ausprobieren</h1>
    <p>Vorbereitete Skripte und Tags bleiben unverändert. Nur „Audio generieren“ verbraucht Credits. Keine LLM-Generierung.</p>
    <p v-if="loading" role="status">Laden …</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <SourceLoader :experiment-key="KEY" :source="source" lead="Lauf und bereits vorbereiteten Audio-Take auswählen. Ohne vorbereitetes Artefakt ist kein Import möglich." selection-label="Vorbereiteten Audio-Take wählen" reset-label="Quelle zurücksetzen" hide-word-budget sample-label="Noch kein vorbereitetes Skript geladen" @loaded="loaded" @error="fail" @sample="source = null; lines = []; setup.artifact_hash = ''" />
    <div v-if="lines.length">
      <h2>ElevenLabs-Eingabe (unverändert)</h2>
      <p v-for="line in lines" :key="line.segment_id"><strong>{{ line.speaker }}:</strong> {{ line.tagged }}</p>
      <label v-for="choice in setup.voice_cast.voices" :key="choice.speaker">{{ choice.speaker }}
        <select v-model="choice.voice_id"><option value="">Stimme wählen</option><option v-for="voice in voices" :key="voice.voice_id" :value="voice.voice_id">{{ voice.name }}</option></select>
      </label>
      <label>Modell <select v-model="setup.voice_cast.model_id"><option v-for="model in models" :key="model">{{ model }}</option></select></label>
      <label>Stabilität <select v-model.number="setup.voice_cast.stability"><option :value="0">Kreativ (0)</option><option :value="0.5">Natürlich (0.5)</option><option :value="1">Robust (1)</option></select></label>
      <label>Seed (optional) <input :value="setup.voice_cast.seed ?? ''" type="number" min="0" max="4294967295" @input="setup.voice_cast.seed = ($event.target as HTMLInputElement).value === '' ? null : Number(($event.target as HTMLInputElement).value)" /></label>
      <p>Ganzes vorbereitetes Skript · Kostenschätzung: ${{ estimate.toFixed(4) }} vor Cache-Wiederverwendung.</p>
      <button class="btn btn--primary" :disabled="!valid || busy" @click="generate">{{ busy ? 'Starten …' : 'Audio generieren (Credits)' }}</button>
    </div>
    <h2>Gespeicherte Versuche</h2>
    <article v-for="run in runs" :key="run.id">
      <p>{{ run.created_at }} · {{ run.setup.voice_cast.model_id }} · {{ run.status }}</p>
      <p v-if="run.error" role="alert">{{ run.error }}</p>
      <button class="btn" @click="restore(run).catch(fail)">Gleiches Skript und Einstellungen laden</button>
      <template v-for="take in takes.filter(take => take.id === run.output?.take_id)" :key="take.id">
        <p>{{ take.status }} · ${{ take.total_cost_usd }} · {{ take.plan.filter(chunk => chunk.status === 'done').length }}/{{ take.plan.length }} Chunks</p>
        <p v-if="take.error" role="alert">{{ take.error }}</p>
        <button v-if="takeActive(take.status)" class="btn" @click="stop(take)">Stoppen</button>
        <button v-if="take.resumable" class="btn" @click="resume(take)">Fortsetzen (Credits)</button>
        <audio v-if="take.mix" controls :src="take.mix.url" />
        <audio v-for="chunk in take.mix ? [] : take.chunks" :key="chunk.index" controls :src="chunk.url" />
      </template>
    </article>
  </section>
</template>

<style scoped>
.stack { display: grid; gap: 1rem; }
label { display: inline-flex; gap: .5rem; margin: .5rem; align-items: center; }
article { border-top: 1px solid var(--rule); padding: 1rem 0; }
</style>
