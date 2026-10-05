<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '@/api/client'
import { sharingApi, type SharedSnapshot } from '@/api/sharing'
import SharedContent from '@/components/SharedContent.vue'

import { t } from '@/i18n'

const props = defineProps<{ token: string }>()
const route = useRoute()
const router = useRouter()
const snapshot = ref<SharedSnapshot | null>(null)
const loading = ref(true)
const unavailable = ref(false)
const error = ref(false)
let generation = 0
const selected = computed(
  () => Number(route.query.episode) || snapshot.value?.episodes[0]?.index || 1,
)
const invalidEpisode = computed(
  () =>
    snapshot.value &&
    !snapshot.value.episodes.some((e) => e.index === selected.value),
)
async function load(): Promise<void> {
  const current = ++generation
  loading.value = true
  snapshot.value = null
  error.value = false
  unavailable.value = false
  try {
    const result = await sharingApi.read(props.token)
    if (current === generation) snapshot.value = result
  } catch (exc) {
    if (current === generation) {
      unavailable.value =
        exc instanceof ApiError && [404, 410].includes(exc.status)
      error.value = !unavailable.value
    }
  } finally {
    if (current === generation) loading.value = false
  }
}
function select(index: number): void {
  void router.push({ query: { episode: String(index) } })
}
onMounted(load)
watch(() => props.token, load)
</script>

<template>
  <main class="public-review">
    <header class="brand">
      <b>{{ t.app.name }}</b
      ><span class="meta">{{ t.sharing.sharedStand }}</span>
    </header>
    <section v-if="loading" class="sheet state" role="status">
      <h1 class="h-section">{{ t.sharing.readerLoading }}</h1>
    </section>
    <section v-else-if="unavailable" class="sheet state">
      <h1 class="h-page">{{ t.sharing.unavailable }}</h1>
      <p>{{ t.sharing.askLink }}</p>
    </section>
    <section v-else-if="error" class="sheet state" role="alert">
      <h1 class="h-section">{{ t.sharing.readError }}</h1>
      <p>{{ t.sharing.retryHint }}</p>
      <button class="btn" @click="load">{{ t.common.retry }}</button>
    </section>
    <template v-else-if="snapshot">
      <p v-if="invalidEpisode" class="notice">{{ t.sharing.invalidEpisode }}</p>
      <SharedContent
        :snapshot="snapshot"
        :selected="selected"
        :token="token"
        @select="select"
      />
    </template>
  </main>
</template>

<style scoped>
.public-review {
  max-width: 1600px;
  margin: 0 auto;
  padding: var(--s5);
}
.brand {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--s3);
  padding: var(--s3) 0 var(--s5);
}
.brand b {
  font-size: var(--t-xl);
  font-weight: 600;
  letter-spacing: -0.025em;
}
.state {
  padding: var(--s7) var(--s6);
}
.state p {
  margin: var(--s5) 0;
  color: var(--ink-2);
  max-width: 60ch;
}
.notice {
  padding: var(--s4);
  background: var(--warn-soft);
  color: var(--warn);
  margin-bottom: var(--s4);
  border-radius: var(--r-md);
}
@media (max-width: 600px) {
  .public-review {
    padding: var(--s3);
  }
  .state {
    padding: var(--s5);
  }
}
</style>
