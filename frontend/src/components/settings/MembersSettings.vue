<script setup lang="ts">
/**
 * Workspace members. Every member is an equal admin, so anyone signed in can
 * add a member, give one a new password or remove one. No mail goes out: the
 * credentials are shown once, right after they were typed or generated, for
 * the person adding the member to pass on, and never again.
 */
import { computed, nextTick, onMounted, ref } from 'vue'

import type { Member } from '@/api/types'
import { t } from '@/i18n'
import { useMembersStore } from '@/stores/members'
import { MIN_PASSWORD_LENGTH, accountError, copyText, generatePassword } from './accounts'

const store = useMembersStore()

const loading = ref(true)
const error = ref('')
const notice = ref('')
/** The credentials just set, shown until dismissed or replaced. */
const handover = ref<{ message: string; email: string; password: string } | null>(null)
const copied = ref(false)

const members = computed(() => store.members)

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    await store.list()
  } catch (exc) {
    error.value = accountError(exc)
  } finally {
    loading.value = false
  }
}

function resetFeedback(): void {
  error.value = ''
  notice.value = ''
  handover.value = null
  copied.value = false
}

async function copyHandover(): Promise<void> {
  if (!handover.value) return
  const ok = await copyText(
    `${t.settings.credentialsEmail}: ${handover.value.email}\n${t.settings.credentialsPassword}: ${handover.value.password}`,
  )
  copied.value = ok
  if (!ok) error.value = t.settings.errors.copyFailed
}

// ----------------------------------------------------------------- add

const draftName = ref('')
const draftEmail = ref('')
const draftPassword = ref('')
const draftVisible = ref(false)
const adding = ref(false)

const canAdd = computed(
  () =>
    draftName.value.trim().length > 0 &&
    draftEmail.value.trim().length > 0 &&
    draftPassword.value.length >= MIN_PASSWORD_LENGTH &&
    !adding.value &&
    !loading.value,
)

const draftCopied = ref(false)

function generateDraft(): void {
  draftPassword.value = generatePassword()
  draftVisible.value = true
  draftCopied.value = false
}

async function copyDraft(): Promise<void> {
  draftCopied.value = await copyText(draftPassword.value)
  if (!draftCopied.value) error.value = t.settings.errors.copyFailed
}

async function addMember(): Promise<void> {
  if (!canAdd.value) return
  resetFeedback()
  adding.value = true
  const password = draftPassword.value
  try {
    const member = await store.create({
      name: draftName.value,
      email: draftEmail.value,
      password,
    })
    handover.value = { message: t.settings.created, email: member.email, password }
    draftName.value = ''
    draftEmail.value = ''
    draftPassword.value = ''
    draftVisible.value = false
    draftCopied.value = false
  } catch (exc) {
    error.value = accountError(exc)
  } finally {
    adding.value = false
  }
}

// ---------------------------------------------------------- new password

const resetting = ref<string | null>(null)
const resetPassword = ref('')
const resetVisible = ref(false)
const resetBusy = ref(false)

async function openReset(member: Member): Promise<void> {
  confirming.value = null
  resetting.value = member.id
  resetPassword.value = ''
  resetVisible.value = false
  await nextTick()
  document.getElementById(`reset-${member.id}`)?.focus()
}

function generateReset(): void {
  resetPassword.value = generatePassword()
  resetVisible.value = true
}

async function applyReset(member: Member): Promise<void> {
  if (resetPassword.value.length < MIN_PASSWORD_LENGTH || resetBusy.value) return
  resetFeedback()
  resetBusy.value = true
  const password = resetPassword.value
  try {
    await store.setPassword(member.id, password)
    handover.value = { message: t.settings.passwordSet, email: member.email, password }
    resetting.value = null
    resetPassword.value = ''
  } catch (exc) {
    error.value = accountError(exc)
  } finally {
    resetBusy.value = false
  }
}

// --------------------------------------------------------------- remove

const confirming = ref<string | null>(null)
const removing = ref(false)

async function askRemove(member: Member): Promise<void> {
  resetting.value = null
  confirming.value = member.id
  await nextTick()
  document.getElementById(`remove-${member.id}`)?.focus()
}

async function applyRemove(member: Member): Promise<void> {
  if (removing.value) return
  resetFeedback()
  removing.value = true
  try {
    await store.remove(member.id)
    confirming.value = null
    notice.value = t.settings.removed
  } catch (exc) {
    error.value = accountError(exc)
  } finally {
    removing.value = false
  }
}

function since(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('de-DE')
}

onMounted(load)
</script>

<template>
  <div class="members">
    <section class="stack" aria-labelledby="members-title">
      <header>
        <h2 id="members-title" class="h-section">
          {{ t.settings.members }}
          <span class="count num">{{ members.length }}</span>
        </h2>
        <p class="muted lead">{{ t.settings.membersLead }}</p>
      </header>

      <p v-if="error" class="banner banner--fail" role="alert">{{ error }}</p>
      <p v-if="notice" class="banner banner--pass" role="status">{{ notice }}</p>
      <div v-if="handover" class="banner banner--pass handover" role="status">
        <p>{{ handover.message }}</p>
        <dl class="creds">
          <dt>{{ t.settings.credentialsEmail }}</dt>
          <dd class="mono">{{ handover.email }}</dd>
          <dt>{{ t.settings.credentialsPassword }}</dt>
          <dd class="mono">{{ handover.password }}</dd>
        </dl>
        <div class="row">
          <button class="btn btn--sm" type="button" @click="copyHandover">
            {{ copied ? t.common.copied : t.settings.copyCredentials }}
          </button>
          <button class="btn btn--sm btn--ghost" type="button" @click="resetFeedback">
            {{ t.settings.dismiss }}
          </button>
        </div>
      </div>

      <p v-if="loading" class="muted">{{ t.common.loading }}</p>
      <ul v-else class="cards">
        <li v-for="member in members" :key="member.id" class="card item">
          <div class="item__row">
            <div class="item__main">
              <div class="item__title">
                <span class="item__name">{{ member.name }}</span>
                <span v-if="member.is_self" class="badge badge--mark">{{ t.settings.you }}</span>
              </div>
              <span class="item__email mono">{{ member.email }}</span>
            </div>
            <span class="item__since mono">{{ t.settings.since }} {{ since(member.created_at) }}</span>
            <div v-if="!member.is_self" class="row item__actions">
              <button class="btn btn--sm" type="button" @click="openReset(member)">
                {{ t.settings.setPassword }}
              </button>
              <button class="btn btn--sm btn--danger" type="button" @click="askRemove(member)">
                {{ t.settings.remove }}
              </button>
            </div>
            <span v-else class="muted item__hint">{{ t.settings.ownPasswordHint }}</span>
          </div>

          <form
            v-if="resetting === member.id"
            class="inline"
            @submit.prevent="applyReset(member)"
            @keydown.esc="resetting = null"
          >
            <p class="muted hint">{{ t.settings.setPasswordLead }}</p>
            <div class="field">
              <label :for="`reset-${member.id}`">
                {{ t.settings.setPasswordFor }} {{ member.name }}
              </label>
              <div class="row">
                <input
                  :id="`reset-${member.id}`"
                  v-model="resetPassword"
                  class="input"
                  :type="resetVisible ? 'text' : 'password'"
                  autocomplete="new-password"
                  :minlength="MIN_PASSWORD_LENGTH"
                  required
                />
                <button class="btn btn--sm btn--ghost" type="button" @click="resetVisible = !resetVisible">
                  {{ resetVisible ? t.settings.hide : t.settings.show }}
                </button>
                <button class="btn btn--sm" type="button" @click="generateReset">
                  {{ t.settings.generate }}
                </button>
              </div>
              <p class="muted hint">{{ t.settings.passwordHint }}</p>
            </div>
            <div class="row end">
              <button class="btn btn--sm btn--ghost" type="button" @click="resetting = null">
                {{ t.common.cancel }}
              </button>
              <button
                class="btn btn--sm btn--primary"
                type="submit"
                :disabled="resetPassword.length < MIN_PASSWORD_LENGTH || resetBusy"
              >
                {{ t.settings.setPassword }}
              </button>
            </div>
          </form>

          <div
            v-if="confirming === member.id"
            class="inline confirm"
            role="group"
            :aria-label="t.settings.removeConfirm"
            @keydown.esc="confirming = null"
          >
            <p>
              <strong>{{ t.settings.removeConfirm }}</strong>
              {{ t.settings.removeLead }}
            </p>
            <div class="row end">
              <button class="btn btn--sm btn--ghost" type="button" @click="confirming = null">
                {{ t.common.cancel }}
              </button>
              <button
                :id="`remove-${member.id}`"
                class="btn btn--sm btn--danger"
                type="button"
                :disabled="removing"
                @click="applyRemove(member)"
              >
                {{ t.settings.removeYes }}
              </button>
            </div>
          </div>
        </li>
      </ul>
    </section>

    <form class="sheet stack" aria-labelledby="add-title" @submit.prevent="addMember">
      <header>
        <h2 id="add-title" class="h-section">{{ t.settings.addMember }}</h2>
        <p class="muted lead">{{ t.settings.addMemberLead }}</p>
      </header>
      <div class="field">
        <label for="member-name">{{ t.settings.name }}</label>
        <input
          id="member-name"
          v-model="draftName"
          class="input"
          autocomplete="off"
          maxlength="200"
          required
        />
      </div>
      <div class="field">
        <label for="member-email">{{ t.settings.email }}</label>
        <input
          id="member-email"
          v-model="draftEmail"
          class="input"
          type="email"
          autocomplete="off"
          maxlength="320"
          required
        />
      </div>
      <div class="field">
        <label for="member-password">{{ t.settings.password }}</label>
        <div class="row">
          <input
            id="member-password"
            v-model="draftPassword"
            class="input"
            :type="draftVisible ? 'text' : 'password'"
            autocomplete="new-password"
            :minlength="MIN_PASSWORD_LENGTH"
            aria-describedby="member-password-hint"
            required
          />
          <button class="btn btn--sm btn--ghost" type="button" @click="draftVisible = !draftVisible">
            {{ draftVisible ? t.settings.hide : t.settings.show }}
          </button>
        </div>
        <div class="row">
          <button class="btn btn--sm" type="button" @click="generateDraft">
            {{ t.settings.generate }}
          </button>
          <button
            class="btn btn--sm"
            type="button"
            :disabled="!draftPassword"
            @click="copyDraft"
          >
            {{ draftCopied ? t.common.copied : t.common.copy }}
          </button>
          <p id="member-password-hint" class="muted hint">{{ t.settings.passwordHint }}</p>
        </div>
      </div>
      <div class="row end">
        <button class="btn btn--primary" type="submit" :disabled="!canAdd">
          {{ t.settings.create }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.members {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
  gap: var(--s5);
  align-items: start;
}

@media (max-width: 920px) {
  .members {
    grid-template-columns: minmax(0, 1fr);
  }
}

.lead {
  margin-top: 6px;
  font-size: var(--t-sm);
}

.count {
  margin-left: var(--s2);
  font-family: var(--mono);
  font-size: var(--t-xs);
  color: var(--ink-3);
}

.hint {
  font-size: var(--t-xs);
}

.mono {
  font-family: var(--mono);
}

.end {
  justify-content: flex-end;
}

.cards {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: var(--s2);
}

.item {
  display: grid;
  gap: var(--s3);
  padding: var(--s3) var(--s4);
}

.item__row {
  display: flex;
  align-items: center;
  gap: var(--s4);
  flex-wrap: wrap;
}

.item__main {
  display: grid;
  gap: 3px;
  flex: 1;
  min-width: 0;
}

.item__title {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
}

.item__name {
  font-size: var(--t-md);
  font-weight: 600;
  letter-spacing: -0.01em;
}

.item__email {
  font-size: var(--t-xs);
  color: var(--ink-3);
  overflow-wrap: anywhere;
}

.item__since {
  font-size: var(--t-xs);
  color: var(--ink-4);
  white-space: nowrap;
}

.item__hint {
  font-size: var(--t-xs);
}

.inline {
  display: grid;
  gap: var(--s3);
  padding: var(--s3);
  border-radius: var(--r-md);
  background: var(--chrome);
}

.confirm {
  background: var(--fail-soft);
  color: var(--ink);
}

.confirm p {
  margin: 0;
  font-size: var(--t-sm);
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

.handover {
  display: grid;
  gap: var(--s2);
}

.handover p {
  margin: 0;
}

.creds {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px var(--s3);
  margin: 0;
  color: var(--ink);
}

.creds dt {
  font-family: var(--mono);
  font-size: var(--t-xs);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.creds dd {
  margin: 0;
  overflow-wrap: anywhere;
  user-select: all;
}
</style>
