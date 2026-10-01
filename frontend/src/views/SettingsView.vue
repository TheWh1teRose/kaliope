<script setup lang="ts">
/**
 * Settings: the signed-in user's profile and the shared workspace.
 *
 * Organisation holds the members, Export the workspace-wide data export that
 * used to be its own menu entry. The active tab is the `tab` query, like the
 * pipelines page, so a link to one tab opens that tab.
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ExportPanel from '@/components/settings/ExportPanel.vue'
import MembersSettings from '@/components/settings/MembersSettings.vue'
import ProfileSettings from '@/components/settings/ProfileSettings.vue'
import { t } from '@/i18n'

const TABS = ['profile', 'organisation', 'export'] as const
type Tab = (typeof TABS)[number]

const LABELS: Record<Tab, string> = {
  profile: t.settings.tabProfile,
  organisation: t.settings.tabOrganisation,
  export: t.settings.tabExport,
}

const route = useRoute()
const router = useRouter()

const tab = computed<Tab>(() => {
  const raw = route.query.tab
  const value = Array.isArray(raw) ? raw[0] : raw
  return value && (TABS as readonly string[]).includes(value) ? (value as Tab) : 'profile'
})

function selectTab(next: Tab): void {
  if (next === tab.value) return
  const query = { ...route.query }
  if (next === 'profile') delete query.tab
  else query.tab = next
  void router.replace({ name: 'settings', query })
}

/** Arrow keys move between tabs, as a tablist should. */
function onKey(event: KeyboardEvent): void {
  const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
  if (!step) return
  event.preventDefault()
  const index = (TABS.indexOf(tab.value) + step + TABS.length) % TABS.length
  selectTab(TABS[index])
  document.getElementById(`settings-tab-${TABS[index]}`)?.focus()
}
</script>

<template>
  <div class="page">
    <header class="head">
      <p class="eyebrow">{{ t.app.name }}</p>
      <h1 class="h-page">{{ t.settings.title }}</h1>
      <p class="muted lead">{{ t.settings.lead }}</p>
    </header>

    <div class="tabs" role="tablist" :aria-label="t.settings.title" @keydown="onKey">
      <button
        v-for="key in TABS"
        :id="`settings-tab-${key}`"
        :key="key"
        class="tab"
        :class="{ 'tab--on': tab === key }"
        role="tab"
        type="button"
        :aria-selected="tab === key"
        :aria-controls="`settings-panel-${key}`"
        :tabindex="tab === key ? 0 : -1"
        @click="selectTab(key)"
      >
        {{ LABELS[key] }}
      </button>
    </div>

    <div
      :id="`settings-panel-${tab}`"
      role="tabpanel"
      :aria-labelledby="`settings-tab-${tab}`"
    >
      <ProfileSettings v-if="tab === 'profile'" />
      <MembersSettings v-else-if="tab === 'organisation'" />
      <ExportPanel v-else />
    </div>
  </div>
</template>

<style scoped>
.page {
  padding: var(--s6);
  max-width: 1180px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--s4);
}

.lead {
  margin-top: 6px;
  font-size: var(--t-sm);
  max-width: 82ch;
}

.tabs {
  display: flex;
  align-items: center;
  gap: var(--s2);
  border-bottom: 1px solid var(--rule);
  padding-bottom: var(--s2);
}

.tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border: 0;
  border-radius: var(--r-md);
  background: transparent;
  color: var(--ink-3);
  font-size: var(--t-sm);
  cursor: pointer;
}

.tab:hover {
  background: var(--chrome);
  color: var(--ink);
}

.tab--on {
  background: var(--ink);
  color: var(--chrome);
}
</style>
