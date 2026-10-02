<script setup lang="ts">
/**
 * "Sammeln in": the folder every "Sammeln" on this experiment page files into,
 * until it is switched. Remembered per experiment in this browser. A folder
 * that no longer exists falls back to "Ohne Ordner".
 */
import { onMounted, ref, watch } from 'vue'

import { ApiError } from '@/api/client'
import { readCollectFolder, writeCollectFolder } from '@/experiments/collectFolder'
import { t } from '@/i18n'
import { useOutputFoldersStore } from '@/stores/folders'

const props = defineProps<{ experimentKey: string }>()
const folderId = defineModel<string | null>({ default: null })

const folders = useOutputFoldersStore()
const labels = t.collection
const creating = ref(false)
const name = ref('')
const error = ref('')

function keepValid(): void {
  if (folderId.value && !folders.byId.has(folderId.value)) folderId.value = null
}

onMounted(async () => {
  folderId.value = readCollectFolder(props.experimentKey)
  try {
    await folders.load()
  } catch {
    /* without the tree the menu just offers "Ohne Ordner" */
  }
  keepValid()
})

watch(folderId, (next) => writeCollectFolder(props.experimentKey, next))
watch(() => folders.items, keepValid)

async function create(): Promise<void> {
  error.value = ''
  try {
    const created = await folders.create(name.value, null)
    folderId.value = created.id
    creating.value = false
    name.value = ''
  } catch (exc) {
    error.value = exc instanceof ApiError ? exc.detail : t.errors.generic
  }
}
</script>

<template>
  <div class="collectbar" role="group" :aria-label="labels.collectIn" :title="labels.collectInHint">
    <label class="eyebrow" :for="`collect-in-${experimentKey}`">{{ labels.collectIn }}</label>
    <select :id="`collect-in-${experimentKey}`" v-model="folderId" class="select">
      <option :value="null">{{ labels.root }}</option>
      <option v-for="folder in folders.items" :key="folder.id" :value="folder.id">
        {{ folder.path.join(' / ') }}
      </option>
    </select>
    <form v-if="creating" class="newfolder" @submit.prevent="create">
      <input
        v-model="name"
        class="input"
        :aria-label="t.folders.name"
        :placeholder="t.folders.name"
        maxlength="200"
      />
      <button class="btn btn--sm btn--primary" type="submit" :disabled="!name.trim()">
        {{ t.common.create }}
      </button>
      <button class="btn btn--sm btn--ghost" type="button" @click="creating = false">
        {{ t.common.cancel }}
      </button>
    </form>
    <button v-else class="btn btn--sm btn--ghost" type="button" @click="creating = true">
      + {{ labels.newFolder }}
    </button>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.collectbar {
  display: flex;
  align-items: center;
  gap: var(--s2);
  flex-wrap: wrap;
  padding: var(--s2) var(--s3);
  margin: var(--s2) 0 var(--s3);
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: var(--r-lg);
}

.select {
  width: auto;
  flex: 1 1 200px;
  min-width: 0;
  padding: 5px 8px;
}

.newfolder {
  display: flex;
  gap: var(--s2);
  flex: 1 1 260px;
  min-width: 0;
}

.newfolder .input {
  flex: 1;
  min-width: 0;
  padding: 5px 8px;
}

.error {
  flex-basis: 100%;
  color: var(--fail);
  font-size: var(--t-sm);
}
</style>
