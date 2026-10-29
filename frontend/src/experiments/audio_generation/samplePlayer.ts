/**
 * One player for every voice sample on the page, so starting a sample stops
 * the one before. Nothing plays until someone presses play.
 */
import { onUnmounted, ref } from 'vue'

export type SampleState = 'idle' | 'loading' | 'playing' | 'failed'

export function useSamplePlayer() {
  /** The voice whose sample is loading, playing or failed; null when silent. */
  const current = ref<string | null>(null)
  const state = ref<SampleState>('idle')
  let audio: HTMLAudioElement | null = null

  function element(): HTMLAudioElement {
    if (audio) return audio
    audio = document.createElement('audio')
    audio.preload = 'none'
    audio.addEventListener('playing', () => {
      if (current.value) state.value = 'playing'
    })
    audio.addEventListener('ended', () => {
      current.value = null
      state.value = 'idle'
    })
    audio.addEventListener('error', () => {
      if (current.value) state.value = 'failed'
    })
    return audio
  }

  function stateOf(id: string): SampleState {
    return current.value === id ? state.value : 'idle'
  }

  function stop(): void {
    audio?.pause()
    current.value = null
    state.value = 'idle'
  }

  /** Play this voice's sample, or pause it when it is the one playing. */
  function toggle(id: string, url: string): void {
    if (current.value === id && (state.value === 'playing' || state.value === 'loading')) {
      stop()
      return
    }
    const player = element()
    player.pause()
    current.value = id
    state.value = 'loading'
    player.src = url
    // Some browsers (and happy-dom) return nothing from play(); only a promise can reject.
    const started = player.play() as Promise<void> | undefined
    if (started) {
      void started.then(
        () => {
          if (current.value === id) state.value = 'playing'
        },
        () => {
          if (current.value === id) state.value = 'failed'
        },
      )
    }
  }

  function fail(id: string): void {
    current.value = id
    state.value = 'failed'
  }

  onUnmounted(stop)
  return { current, state, stateOf, toggle, stop, fail }
}
