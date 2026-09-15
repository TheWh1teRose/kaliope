<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { t } from '@/i18n'
import { useAuthStore } from '@/stores/auth'
import { useCatalogueStore } from '@/stores/catalogue'

const auth = useAuthStore()
const catalogue = useCatalogueStore()
const route = useRoute()

const theme = ref<'light' | 'dark' | 'system'>(
  (localStorage.getItem('kalliope-theme') as 'light' | 'dark' | 'system') ?? 'system',
)

function applyTheme(): void {
  const root = document.documentElement
  if (theme.value === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', theme.value)
  localStorage.setItem('kalliope-theme', theme.value)
}

function cycleTheme(): void {
  theme.value = theme.value === 'system' ? 'light' : theme.value === 'light' ? 'dark' : 'system'
  applyTheme()
}

const chrome = computed(() => Boolean(auth.user) && route.name !== 'login')

onMounted(async () => {
  applyTheme()
  const user = await auth.refresh()
  if (user) await catalogue.load().catch(() => undefined)
})
</script>

<template>
  <div class="shell" :class="{ 'shell--bare': !chrome }">
    <nav v-if="chrome" class="rail">
      <RouterLink :to="{ name: 'documents' }" class="rail__mark" :aria-label="t.app.name">
        <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden="true">
          <path d="M16 4v24M4 16h24" stroke="currentColor" stroke-width="1" opacity=".45" />
          <circle cx="16" cy="16" r="6.5" fill="none" stroke="currentColor" stroke-width="2.5" />
        </svg>
      </RouterLink>

      <RouterLink :to="{ name: 'documents' }" class="rail__link" :title="t.nav.documents">
        <span aria-hidden="true">◫</span>
        <span class="sr-only">{{ t.nav.documents }}</span>
      </RouterLink>
      <RouterLink :to="{ name: 'runs' }" class="rail__link" :title="t.nav.runs">
        <span aria-hidden="true">≡</span>
        <span class="sr-only">{{ t.nav.runs }}</span>
      </RouterLink>
      <RouterLink :to="{ name: 'gates' }" class="rail__link" :title="t.nav.gates">
        <span aria-hidden="true">⊘</span>
        <span class="sr-only">{{ t.nav.gates }}</span>
      </RouterLink>
      <RouterLink :to="{ name: 'pipelines' }" class="rail__link" :title="t.nav.pipelines">
        <span aria-hidden="true">⧉</span>
        <span class="sr-only">{{ t.nav.pipelines }}</span>
      </RouterLink>
      <RouterLink :to="{ name: 'bench' }" class="rail__link" :title="t.nav.bench">
        <span aria-hidden="true">⚒</span>
        <span class="sr-only">{{ t.nav.bench }}</span>
      </RouterLink>
      <RouterLink :to="{ name: 'exports' }" class="rail__link" :title="t.nav.exports">
        <span aria-hidden="true">↧</span>
        <span class="sr-only">{{ t.nav.exports }}</span>
      </RouterLink>

      <div class="grow" />

      <button class="rail__link" :title="t.nav.theme" @click="cycleTheme">
        <span aria-hidden="true">◐</span>
        <span class="sr-only">{{ t.nav.theme }}</span>
      </button>
      <button class="rail__link" :title="t.nav.logout" @click="auth.logout()">
        <span aria-hidden="true">⏻</span>
        <span class="sr-only">{{ t.nav.logout }}</span>
      </button>
      <span class="rail__who" :title="auth.user?.email">{{
        (auth.user?.name ?? '?').slice(0, 2).toUpperCase()
      }}</span>
    </nav>

    <main class="content scroll">
      <RouterView v-slot="{ Component }">
        <Transition name="view" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: var(--rail) 1fr;
  height: 100%;
}

.shell--bare {
  grid-template-columns: 1fr;
}

.rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--s1);
  padding: var(--s3) 0 var(--s4);
  background: var(--chrome);
  border-right: 1px solid var(--rule);
}

.rail__mark {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  margin-bottom: var(--s4);
  color: var(--ink);
  border-radius: var(--r-md);
}

.rail__mark:hover {
  color: var(--mark);
}

.rail__link {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: var(--r-md);
  background: transparent;
  color: var(--ink-3);
  font-size: 1.05rem;
  cursor: pointer;
  text-decoration: none;
  transition:
    background var(--fast),
    color var(--fast);
}

.rail__link:hover {
  background: var(--sunk);
  color: var(--ink);
}

.rail__link.router-link-active {
  background: var(--ink);
  color: var(--chrome);
}

.rail__who {
  margin-top: var(--s3);
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--mark-soft);
  color: var(--mark-deep);
  font-family: var(--mono);
  font-size: 0.625rem;
  letter-spacing: 0.04em;
}

.content {
  min-width: 0;
  height: 100%;
}

.view-enter-active,
.view-leave-active {
  transition:
    opacity var(--fast),
    transform var(--fast);
}

.view-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

.view-leave-to {
  opacity: 0;
}
</style>
