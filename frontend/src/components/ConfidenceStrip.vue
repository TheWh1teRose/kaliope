<script setup lang="ts">
/**
 * The ingestion report as five measured bands (§5.8). Each segment is one
 * property the confidence score is derived from, coloured by the band it fell
 * into — so a low-confidence parse shows *which* measurement caused it without
 * anyone opening the detail panel.
 */
import { computed } from 'vue'

import type { IngestionReport } from '@/api/types'
import { t } from '@/i18n'

const props = defineProps<{ report: IngestionReport; compact?: boolean }>()

type Band = 'pass' | 'warn' | 'fail'

interface Measure {
  key: string
  label: string
  value: string
  band: Band
  title: string
}

function band(value: number, good: number, acceptable: number): Band {
  if (value >= good) return 'pass'
  if (value >= acceptable) return 'warn'
  return 'fail'
}

const measures = computed<Measure[]>(() => {
  const r = props.report
  const structureBand: Band =
    r.structure_confidence === 'high' ? 'pass' : r.structure_confidence === 'medium' ? 'warn' : 'fail'
  return [
    {
      key: 'structure',
      label: t.report.structure,
      value: t.report.structureSource[r.structure_source],
      band: structureBand,
      title: `${t.report.structure}: ${t.report.structureSource[r.structure_source]} (${r.section_count} ${t.report.sections})`,
    },
    {
      key: 'anchors',
      label: t.report.anchors,
      value: `${Math.round(r.anchor_integrity * 100)}%`,
      band: band(r.anchor_integrity, 0.99, 0.95),
      title: `${t.report.anchors}: ${(r.anchor_integrity * 100).toFixed(1)}%`,
    },
    {
      key: 'zones',
      label: t.report.zones,
      value: `${Math.round(r.zone_uncertain_ratio * 100)}% ${t.report.zoneUncertain}`,
      band: band(1 - r.zone_uncertain_ratio, 0.85, 0.65),
      title: `${t.report.zones}: ${(r.zone_uncertain_ratio * 100).toFixed(0)}% ${t.report.zoneUncertain}`,
    },
    {
      key: 'density',
      label: t.report.density,
      value: `${Math.round(r.text_density)}`,
      band: band(r.text_density, 150, 60),
      title: `${t.report.density}: ${Math.round(r.text_density)} ${t.report.densityUnit}`,
    },
    {
      key: 'order',
      label: t.report.readingOrder,
      value: `${Math.round(r.reading_order_confidence * 100)}%`,
      band: band(r.reading_order_confidence, 0.9, 0.75),
      title: `${t.report.readingOrder}: ${(r.reading_order_confidence * 100).toFixed(0)}%`,
    },
  ]
})
</script>

<template>
  <div class="strip" :class="{ 'strip--compact': compact }">
    <div
      v-for="m in measures"
      :key="m.key"
      class="strip__cell"
      :class="`strip__cell--${m.band}`"
      :title="m.title"
    >
      <span class="strip__label">{{ m.label }}</span>
      <span class="strip__value num">{{ m.value }}</span>
    </div>
  </div>
</template>

<style scoped>
.strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 2px;
  border-radius: var(--r-sm);
  overflow: hidden;
  background: var(--rule);
}

.strip__cell {
  padding: 7px 9px;
  background: var(--card);
  border-top: 2px solid var(--idle);
  min-width: 0;
}

.strip--compact .strip__cell {
  padding: 5px 7px;
}

.strip__cell--pass {
  border-top-color: var(--pass);
}
.strip__cell--warn {
  border-top-color: var(--warn);
}
.strip__cell--fail {
  border-top-color: var(--fail);
}

.strip__label {
  display: block;
  font-family: var(--mono);
  font-size: 0.625rem;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.strip__value {
  display: block;
  font-size: var(--t-sm);
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
