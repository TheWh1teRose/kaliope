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
        <span class="rail__icon" aria-hidden="true">◫</span>
        <span class="rail__label">{{ t.nav.documents }}</span>
      </RouterLink>
      <RouterLink :to="{ name: 'runs' }" class="rail__link" :title="t.nav.runs">
        <span class="rail__icon" aria-hidden="true">≡</span>
        <span class="rail__label">{{ t.nav.runs }}</span>
      </RouterLink>
      <RouterLink :to="{ name: 'pipelines' }" class="rail__link" :title="t.nav.pipelines">
        <span class="rail__icon" aria-hidden="true">⧉</span>
        <span class="rail__label">{{ t.nav.pipelines }}</span>
      </RouterLink>
      <div class="rail__group" role="group" aria-labelledby="rail-experimenting">
        <span id="rail-experimenting" class="rail__head">
          <span class="rail__icon" aria-hidden="true">⌬</span>
          <span class="rail__label">{{ t.nav.experimenting }}</span>
        </span>
        <div class="rail__sub">
          <RouterLink :to="{ name: 'bench' }" class="rail__link" :title="t.nav.bench">
            <span class="rail__label">{{ t.nav.bench }}</span>
          </RouterLink>
          <RouterLink :to="{ name: 'experiments' }" class="rail__link" :title="t.nav.experiments">
            <span class="rail__label">{{ t.nav.experiments }}</span>
          </RouterLink>
        </div>
      </div>

      <div class="grow" />

      <button class="rail__link" :title="t.nav.theme" @click="cycleTheme">
        <span class="rail__icon" aria-hidden="true">◐</span>
        <span class="rail__label">{{ t.nav.theme }}</span>
      </button>
      <button class="rail__link" :title="t.nav.logout" @click="auth.logout()">
        <span class="rail__icon" aria-hidden="true">⏻</span>
        <span class="rail__label">{{ t.nav.logout }}</span>
      </button>
      <RouterLink
        :to="{ name: 'settings' }"
        class="rail__link rail__user"
        :title="`${t.nav.settings} · ${auth.user?.email ?? ''}`"
      >
        <span class="rail__who" aria-hidden="true">{{
          (auth.user?.name ?? '?').slice(0, 2).toUpperCase()
        }}</span>
        <span class="rail__label">
          <span class="rail__name">{{ auth.user?.name }}</span>
          <span class="rail__sub-label">{{ t.nav.settings }}</span>
        </span>
      </RouterLink>
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
  grid-template-columns: var(--rail-wide) 1fr;
  height: 100%;
}

.shell--bare {
  grid-template-columns: 1fr;
}

.rail {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--s1);
  padding: var(--s3) var(--s2) var(--s4);
  min-width: 0;
  background: var(--chrome);
  border-right: 1px solid var(--rule);
}

.rail__mark {
  display: grid;
  place-items: center;
  align-self: center;
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
  display: flex;
  align-items: center;
  gap: var(--s2);
  min-height: 34px;
  padding: 0 var(--s2);
  min-width: 0;
  border: 0;
  text-align: left;
  font: inherit;
  font-size: 0.8125rem;
  border-radius: var(--r-md);
  background: transparent;
  color: var(--ink-3);
  cursor: pointer;
  text-decoration: none;
  transition:
    background var(--fast),
    color var(--fast);
}

.rail__icon {
  flex: none;
  width: 1.25rem;
  text-align: center;
  font-size: 1.05rem;
}

.rail__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rail__group {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: var(--s1) 0;
}

.rail__head {
  display: flex;
  align-items: center;
  gap: var(--s2);
  min-height: 30px;
  padding: 0 var(--s2);
  font-size: 0.8125rem;
  font-weight: 560;
  color: var(--ink-2);
}

.rail__sub {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-left: calc(var(--s2) + 0.6rem);
  padding-left: calc(var(--s2) + 2px);
  border-left: 1px solid var(--rule-strong);
}

.rail__sub .rail__link {
  min-height: 30px;
}

.rail__link:hover {
  background: var(--sunk);
  color: var(--ink);
}

.rail__link.router-link-active {
  background: var(--ink);
  color: var(--chrome);
}

.rail__user {
  margin-top: var(--s3);
  min-height: 42px;
  padding: var(--s1) var(--s2);
}

.rail__user .rail__label {
  display: grid;
  line-height: 1.25;
}

.rail__name {
  overflow: hidden;
  text-overflow: ellipsis;
  color: inherit;
  font-weight: 560;
}

.rail__sub-label {
  font-size: var(--t-xs);
  opacity: 0.75;
}

.rail__who {
  flex: none;
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
