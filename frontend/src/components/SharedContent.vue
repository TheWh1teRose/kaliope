<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { sharedDate, sharedDuration, type SharedSnapshot } from '@/api/sharing'

import { fill, t } from '@/i18n'

const props = defineProps<{ snapshot: SharedSnapshot; selected: number }>()
const emit = defineEmits<{ (e: 'select', index: number): void }>()
const position = computed(() =>
  Math.max(
    0,
    props.snapshot.episodes.findIndex((e) => e.index === props.selected),
  ),
)
const episode = computed(() => props.snapshot.episodes[position.value])
const player = ref<HTMLAudioElement | null>(null)
const audioFailed = ref(false)
watch(episode, () => {
  player.value?.pause()
  audioFailed.value = false
})
onUnmounted(() => player.value?.pause())
function retry(): void {
  audioFailed.value = false
  player.value?.load()
}
</script>

<template>
  <div class="shared-content">
    <header class="head">
      <p class="eyebrow">{{ t.sharing.intro }}</p>
      <h1>{{ snapshot.title }}</h1>
      <p class="meta">
        {{
          fill(t.sharing.readerDates, {
            created: sharedDate(snapshot.created_at),
            expires: sharedDate(snapshot.expires_at),
          })
        }}
      </p>
    </header>
    <p v-if="!snapshot.episodes.length" class="empty">{{ t.sharing.empty }}</p>
    <div
      v-else
      class="layout"
      :class="{ single: snapshot.episodes.length === 1 }"
    >
      <nav
        v-if="snapshot.episodes.length > 1"
        class="episodes"
        :aria-label="t.sharing.manage"
      >
        <p class="eyebrow">
          {{ fill(t.sharing.episodes, { n: snapshot.episodes.length }) }}
        </p>
        <button
          v-for="item in snapshot.episodes"
          :key="item.index"
          type="button"
          :aria-current="item.index === episode.index ? 'true' : undefined"
          @click="emit('select', item.index)"
        >
          <span class="num">{{ item.index }}</span
          ><span
            ><b>{{ item.title }}</b
            ><small>{{
              item.audio ? t.sharing.audioPresent : t.sharing.scriptOnly
            }}</small></span
          >
        </button>
      </nav>
      <article v-if="episode" class="reading">
        <div v-if="snapshot.episodes.length > 1" class="mobile-select field">
          <label for="shared-episode">{{ t.sharing.selectEpisode }}</label>
          <select
            id="shared-episode"
            class="select"
            :value="episode.index"
            @change="
              emit('select', Number(($event.target as HTMLSelectElement).value))
            "
          >
            <option
              v-for="item in snapshot.episodes"
              :key="item.index"
              :value="item.index"
            >
              {{ item.index }} · {{ item.title
              }}{{ item.audio ? '' : ' · ' + t.sharing.scriptOnly }}
            </option>
          </select>
        </div>
        <p class="eyebrow">
          {{
            fill(t.sharing.progress, {
              n: position + 1,
              total: snapshot.episodes.length,
            })
          }}
        </p>
        <h2 class="h-section">{{ episode.title }}</h2>
        <div v-if="episode.audio" class="audio">
          <div class="row wrap">
            <span class="badge badge--pass">{{ t.sharing.fullEpisode }}</span
            ><span class="meta num">{{
              sharedDuration(episode.audio.duration_s)
            }}</span>
          </div>
          <audio
            :key="episode.index + ':' + episode.audio.url"
            ref="player"
            controls
            preload="metadata"
            :src="episode.audio.url"
            :aria-label="t.sharing.audioLabel"
            @error="audioFailed = true"
          />
          <div v-if="audioFailed" class="notice notice--fail" role="alert">
            <p>{{ t.sharing.playbackError }}</p>
            <button class="btn btn--sm" type="button" @click="retry">
              {{ t.sharing.retryAudio }}
            </button>
          </div>
        </div>
        <p v-else class="notice" role="status">{{ t.sharing.noAudio }}</p>
        <p class="eyebrow script-label">{{ t.sharing.script }}</p>
        <p v-if="!episode.segments.length" class="empty">
          {{ t.sharing.empty }}
        </p>
        <section
          v-for="(segment, index) in episode.segments"
          :key="index"
          class="segment"
        >
          <h3>{{ segment.speaker }}</h3>
          <p class="prose">{{ segment.text }}</p>
        </section>
        <footer v-if="snapshot.episodes.length > 1" class="spread wrap">
          <button
            class="btn"
            :disabled="position === 0"
            @click="emit('select', snapshot.episodes[position - 1].index)"
          >
            {{ t.sharing.previous }}
          </button>
          <button
            class="btn"
            :disabled="position === snapshot.episodes.length - 1"
            @click="emit('select', snapshot.episodes[position + 1].index)"
          >
            {{ t.sharing.next }}
          </button>
        </footer>
      </article>
    </div>
  </div>
</template>

<style scoped>
.shared-content {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
  overflow: hidden;
}
.head {
  padding: var(--s6);
  border-bottom: 1px solid var(--rule);
}
h1 {
  font: 2.3rem/1.2 var(--serif);
  letter-spacing: -0.025em;
  margin: var(--s3) 0;
  overflow-wrap: anywhere;
}
.meta {
  overflow-wrap: anywhere;
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 0.65fr) minmax(0, 1.8fr);
}
.layout > * {
  min-width: 0;
}
.layout.single {
  grid-template-columns: minmax(0, 1fr);
}
.episodes {
  padding: var(--s4);
  background: var(--chrome);
  border-right: 1px solid var(--rule);
}
.episodes > p {
  margin: var(--s2) var(--s2) var(--s4);
}
.episodes button {
  width: 100%;
  display: flex;
  gap: var(--s3);
  text-align: left;
  padding: var(--s3);
  border: 1px solid transparent;
  border-radius: var(--r-md);
  background: transparent;
  cursor: pointer;
}
.episodes button[aria-current] {
  background: var(--mark-soft);
  border-color: var(--rule);
}
.episodes b {
  font-size: var(--t-sm);
  font-weight: 550;
  overflow-wrap: anywhere;
}
.episodes small {
  display: block;
  color: var(--ink-2);
  font: var(--t-xs) var(--mono);
  margin-top: 6px;
}
.reading {
  padding: var(--s6);
}
h2 {
  margin: var(--s2) 0 var(--s4);
  overflow-wrap: anywhere;
}
audio {
  display: block;
  width: 100%;
  margin: var(--s4) 0;
}
.notice {
  padding: var(--s4);
  border: 1px solid var(--rule);
  border-radius: var(--r-md);
  background: var(--chrome);
  font-size: var(--t-sm);
}
.notice--fail {
  color: var(--fail);
  border-color: var(--fail);
  background: var(--fail-soft);
}
.notice button {
  margin-top: var(--s3);
}
.script-label {
  margin: var(--s5) 0;
}
.segment {
  padding-bottom: var(--s5);
  margin-bottom: var(--s5);
  border-bottom: 1px solid var(--rule);
}
.segment h3 {
  font-size: var(--t-sm);
  margin-bottom: var(--s2);
}
.segment p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.empty {
  padding: var(--s5);
  color: var(--ink-2);
}
.mobile-select {
  display: none;
  margin-bottom: var(--s5);
}
@media (max-width: 760px) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .episodes {
    display: none;
  }
  .mobile-select {
    display: flex;
  }
  .head,
  .reading {
    padding: var(--s5);
  }
  h1 {
    font-size: 1.9rem;
  }
}
</style>
