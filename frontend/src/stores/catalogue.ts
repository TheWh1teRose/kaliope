import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import type { FlowOut, FormatSpec, GateSpec, ReasonCodeSpec, ZoneSpec } from '@/api/types'

/** Server-owned enumerations: flows, formats, gates, reason codes and zones. */
export const useCatalogueStore = defineStore('catalogue', () => {
  const flows = ref<FlowOut[]>([])
  const formats = ref<FormatSpec[]>([])
  const reasonCodes = ref<ReasonCodeSpec[]>([])
  const zones = ref<ZoneSpec[]>([])
  const gates = ref<GateSpec[]>([])
  const loaded = ref(false)

  async function load(): Promise<void> {
    if (loaded.value) return
    const [f, fm, rc, z, g] = await Promise.all([
      api.get<FlowOut[]>('/api/flows'),
      api.get<FormatSpec[]>('/api/formats'),
      api.get<ReasonCodeSpec[]>('/api/review/reason-codes'),
      api.get<ZoneSpec[]>('/api/review/zones'),
      api.get<GateSpec[]>('/api/gates'),
    ])
    flows.value = f
    formats.value = fm
    reasonCodes.value = rc
    zones.value = z
    gates.value = g
    loaded.value = true
  }

  function zoneSpec(zone: string): ZoneSpec | undefined {
    return zones.value.find((z) => z.zone === zone)
  }

  function reasonSpec(code: string): ReasonCodeSpec | undefined {
    return reasonCodes.value.find((r) => r.code === code)
  }

  function gateSpec(id: string): GateSpec | undefined {
    return gates.value.find((g) => g.id === id)
  }

  return { flows, formats, reasonCodes, zones, gates, loaded, load, zoneSpec, reasonSpec, gateSpec }
})
