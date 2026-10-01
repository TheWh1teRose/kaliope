<script setup lang="ts">
/**
 * The signed-in user's own account: display name, login address and password.
 * Changing the password needs the current one and signs every other browser
 * out; this session stays signed in either way.
 */
import { computed, ref, watch } from 'vue'

import { t } from '@/i18n'
import { useAuthStore } from '@/stores/auth'
import { MIN_PASSWORD_LENGTH, accountError } from './accounts'

const auth = useAuthStore()

const name = ref(auth.user?.name ?? '')
const email = ref(auth.user?.email ?? '')
const profileBusy = ref(false)
const profileError = ref('')
const profileSaved = ref(false)

watch(
  () => auth.user,
  (user) => {
    name.value = user?.name ?? ''
    email.value = user?.email ?? ''
  },
)

const profileDirty = computed(
  () =>
    name.value.trim() !== (auth.user?.name ?? '') ||
    email.value.trim().toLowerCase() !== (auth.user?.email ?? ''),
)

async function saveProfile(): Promise<void> {
  profileBusy.value = true
  profileError.value = ''
  profileSaved.value = false
  try {
    await auth.updateProfile({ name: name.value, email: email.value })
    profileSaved.value = true
  } catch (exc) {
    profileError.value = accountError(exc)
  } finally {
    profileBusy.value = false
  }
}

const current = ref('')
const next = ref('')
const repeat = ref('')
const passwordBusy = ref(false)
const passwordError = ref('')
const passwordChanged = ref(false)

const mismatch = computed(() => repeat.value.length > 0 && next.value !== repeat.value)
const canChange = computed(
  () =>
    current.value.length > 0 &&
    next.value.length >= MIN_PASSWORD_LENGTH &&
    next.value === repeat.value &&
    !passwordBusy.value,
)

async function changePassword(): Promise<void> {
  if (!canChange.value) return
  passwordBusy.value = true
  passwordError.value = ''
  passwordChanged.value = false
  try {
    await auth.changePassword(current.value, next.value)
    current.value = ''
    next.value = ''
    repeat.value = ''
    passwordChanged.value = true
  } catch (exc) {
    passwordError.value = accountError(exc)
  } finally {
    passwordBusy.value = false
  }
}
</script>

<template>
  <div class="profile">
    <form class="sheet stack" aria-labelledby="profile-title" @submit.prevent="saveProfile">
      <header>
        <h2 id="profile-title" class="h-section">{{ t.settings.profile }}</h2>
        <p class="muted lead">{{ t.settings.profileLead }}</p>
      </header>
      <div class="field">
        <label for="profile-name">{{ t.settings.name }}</label>
        <input
          id="profile-name"
          v-model="name"
          class="input"
          autocomplete="name"
          maxlength="200"
          required
          @input="profileSaved = false"
        />
      </div>
      <div class="field">
        <label for="profile-email">{{ t.settings.email }}</label>
        <input
          id="profile-email"
          v-model="email"
          class="input"
          type="email"
          autocomplete="email"
          maxlength="320"
          required
          @input="profileSaved = false"
        />
      </div>
      <p v-if="profileError" class="banner banner--fail" role="alert">{{ profileError }}</p>
      <p v-if="profileSaved" class="banner banner--pass" role="status">
        {{ t.settings.profileSaved }}
      </p>
      <div class="actions">
        <button
          class="btn btn--primary"
          type="submit"
          :disabled="profileBusy || !profileDirty || !name.trim() || !email.trim()"
        >
          {{ profileBusy ? t.common.saving : t.settings.saveProfile }}
        </button>
      </div>
    </form>

    <form class="sheet stack" aria-labelledby="password-title" @submit.prevent="changePassword">
      <header>
        <h2 id="password-title" class="h-section">{{ t.settings.password }}</h2>
        <p class="muted lead">{{ t.settings.passwordLead }}</p>
      </header>
      <div class="field">
        <label for="password-current">{{ t.settings.currentPassword }}</label>
        <input
          id="password-current"
          v-model="current"
          class="input"
          type="password"
          autocomplete="current-password"
          required
        />
      </div>
      <div class="field">
        <label for="password-new">{{ t.settings.newPassword }}</label>
        <input
          id="password-new"
          v-model="next"
          class="input"
          type="password"
          autocomplete="new-password"
          :minlength="MIN_PASSWORD_LENGTH"
          aria-describedby="password-new-hint"
          required
        />
        <p id="password-new-hint" class="muted hint">{{ t.settings.passwordHint }}</p>
      </div>
      <div class="field">
        <label for="password-repeat">{{ t.settings.repeatPassword }}</label>
        <input
          id="password-repeat"
          v-model="repeat"
          class="input"
          type="password"
          autocomplete="new-password"
          :aria-invalid="mismatch"
          required
        />
        <p v-if="mismatch" class="hint hint--fail" role="alert">
          {{ t.settings.passwordMismatch }}
        </p>
      </div>
      <p v-if="passwordError" class="banner banner--fail" role="alert">{{ passwordError }}</p>
      <p v-if="passwordChanged" class="banner banner--pass" role="status">
        {{ t.settings.passwordChanged }}
      </p>
      <div class="actions">
        <button class="btn btn--primary" type="submit" :disabled="!canChange">
          {{ passwordBusy ? t.common.saving : t.settings.changePassword }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.profile {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr));
  gap: var(--s4);
  align-items: start;
}

.lead {
  margin-top: 6px;
  font-size: var(--t-sm);
}

.hint {
  font-size: var(--t-xs);
}

.hint--fail {
  color: var(--fail);
}

.actions {
  display: flex;
  justify-content: flex-end;
}

.banner {
  margin: 0;
  padding: var(--s3) var(--s4);
  border-radius: var(--r-md);
  font-size: var(--t-sm);
}

.banner--fail {
  background: var(--fail-soft);
  color: var(--fail);
}

.banner--pass {
  background: var(--pass-soft);
  color: var(--pass);
}
</style>
