<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { t } from '@/i18n'
import { useAuthStore } from '@/stores/auth'
import { useCatalogueStore } from '@/stores/catalogue'

const auth = useAuthStore()
const catalogue = useCatalogueStore()
const router = useRouter()
const route = useRoute()

const email = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')

async function submit(): Promise<void> {
  busy.value = true
  error.value = ''
  try {
    await auth.login(email.value, password.value)
    await catalogue.load().catch(() => undefined)
    const next = typeof route.query.next === 'string' ? route.query.next : '/documents'
    await router.replace(next)
  } catch {
    error.value = t.login.failed
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="gate">
    <!-- The registration mark that names the product: two plates coming into
         alignment, which is what the console is for. -->
    <div class="gate__art" aria-hidden="true">
      <svg viewBox="0 0 200 200">
        <rect class="plate plate--a" x="34" y="42" width="104" height="116" rx="2" />
        <rect class="plate plate--b" x="62" y="42" width="104" height="116" rx="2" />
        <line x1="100" y1="14" x2="100" y2="186" />
        <line x1="14" y1="100" x2="186" y2="100" />
        <circle cx="100" cy="100" r="26" />
        <circle cx="100" cy="100" r="7" />
      </svg>
    </div>

    <form class="gate__form" @submit.prevent="submit">
      <p class="eyebrow">{{ t.app.tagline }}</p>
      <h1 class="h-page">{{ t.app.name }}</h1>
      <p class="muted lead">{{ t.login.lead }}</p>

      <div class="field">
        <label for="email">{{ t.login.email }}</label>
        <input
          id="email"
          v-model="email"
          class="input"
          type="email"
          autocomplete="username"
          required
          autofocus
        />
      </div>

      <div class="field">
        <label for="password">{{ t.login.password }}</label>
        <input
          id="password"
          v-model="password"
          class="input"
          type="password"
          autocomplete="current-password"
          required
        />
      </div>

      <p v-if="error" class="error" role="alert">{{ error }}</p>

      <button class="btn btn--primary" type="submit" :disabled="busy">
        {{ busy ? t.common.loading : t.login.submit }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.gate {
  display: grid;
  grid-template-columns: 1fr minmax(320px, 400px);
  height: 100%;
}

.gate__art {
  display: grid;
  place-items: center;
  background: var(--chrome);
  border-right: 1px solid var(--rule);
  padding: var(--s7);
}

.gate__art svg {
  width: min(360px, 60%);
  color: var(--ink);
}

.gate__art line {
  stroke: currentColor;
  stroke-width: 0.6;
  opacity: 0.35;
  stroke-dasharray: 6 4;
}

.gate__art circle {
  fill: none;
  stroke: var(--mark);
  stroke-width: 1.4;
}

.plate {
  fill: none;
  stroke: currentColor;
  stroke-width: 1;
  opacity: 0.5;
}

.plate--a {
  animation: registerA 5s cubic-bezier(0.16, 1, 0.3, 1) infinite alternate;
}

.plate--b {
  stroke: var(--mark);
  animation: registerB 5s cubic-bezier(0.16, 1, 0.3, 1) infinite alternate;
}

@keyframes registerA {
  0%,
  40% {
    transform: translateX(0);
  }
  100% {
    transform: translateX(14px);
  }
}

@keyframes registerB {
  0%,
  40% {
    transform: translateX(0);
  }
  100% {
    transform: translateX(-14px);
  }
}

.gate__form {
  display: flex;
  flex-direction: column;
  gap: var(--s4);
  justify-content: center;
  padding: var(--s7) var(--s6);
}

.lead {
  font-size: var(--t-sm);
  margin-bottom: var(--s2);
}

.error {
  font-size: var(--t-sm);
  color: var(--fail);
}

@media (max-width: 760px) {
  .gate {
    grid-template-columns: 1fr;
  }
  .gate__art {
    display: none;
  }
}
</style>
