import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ModelCatalogue, ModelInfo, NodeParam } from '@/api/types'

import NodeParams from './NodeParams.vue'

const base: Omit<ModelInfo, 'id'> = {
  provider: 'anthropic',
  input_usd_per_mtok: 3,
  output_usd_per_mtok: 15,
  max_output_tokens: 64_000,
  supports_sampling: false,
  supports_top_k: false,
  sampling_with_thinking: false,
  thinking_modes: ['adaptive', 'off'],
  thinking_default: 'on',
  min_thinking_budget: 1024,
  thinking_off_max_effort: 'high',
  effort_levels: ['low', 'medium', 'high', 'xhigh', 'max'],
  default_effort: 'high',
}

const catalogue: ModelCatalogue = {
  default_model: 'claude-haiku-4-5',
  models: [
    { ...base, id: 'claude-opus-5' },
    {
      ...base,
      id: 'claude-opus-4-6',
      effort_levels: ['low', 'medium', 'high', 'max'],
    },
    {
      ...base,
      id: 'claude-haiku-4-5',
      effort_levels: [],
      default_effort: null,
      thinking_modes: ['budget'],
    },
  ],
  providers: [{ name: 'anthropic' }],
}

const params: NodeParam[] = [
  {
    key: 'model',
    label: 'Model',
    type: 'model',
    default: null,
    description: null,
    options: [],
    minimum: null,
    maximum: null,
    advanced: false,
  },
  {
    key: 'effort',
    label: 'Aufwand',
    type: 'select',
    default: null,
    description: null,
    options: ['low', 'medium', 'high', 'xhigh', 'max'],
    minimum: null,
    maximum: null,
    advanced: false,
  },
  {
    key: 'temperature',
    label: 'Temperature',
    type: 'float',
    default: 0.7,
    description: null,
    options: [],
    minimum: 0,
    maximum: 2,
    advanced: false,
  },
]

function render(config: Record<string, unknown>) {
  return mount(NodeParams, {
    props: {
      params,
      config,
      models: ['claude-opus-5', 'claude-opus-4-6', 'claude-haiku-4-5'],
      catalogue,
    },
  })
}

describe('node effort', () => {
  it('offers the effort levels of the selected model', () => {
    const wrapper = render({ model: 'claude-opus-5' })
    const effort = wrapper.get('[data-param="effort"] select')
    expect(wrapper.get('[data-param="effort"]').text()).toContain('Aufwand')
    expect(effort.findAll('option').map((option) => option.text())).toEqual([
      'Standard (hoch)',
      'niedrig',
      'mittel',
      'hoch',
      'extra hoch',
      'maximal',
    ])
    expect(wrapper.findAll('[data-param="effort"]')).toHaveLength(1)
  })

  it('shows no levels for a model that does not accept effort', () => {
    const wrapper = render({ model: 'claude-haiku-4-5' })
    const effort = wrapper.get('[data-param="effort"] select')
    expect(effort.attributes('disabled')).toBeDefined()
    expect(effort.findAll('option').map((option) => option.text())).toEqual(['nicht unterstützt'])
  })

  it('clears an effort the newly selected model does not support', async () => {
    const wrapper = render({ model: 'claude-opus-5', effort: 'xhigh' })
    await wrapper.get('[data-param="model"] select').setValue('claude-opus-4-6')
    expect(wrapper.emitted('update')).toEqual([
      ['model', 'claude-opus-4-6'],
      ['effort', null],
    ])
  })

  it('keeps an effort the new model still supports', async () => {
    const wrapper = render({ model: 'claude-opus-5', effort: 'max' })
    await wrapper.get('[data-param="model"] select').setValue('claude-opus-4-6')
    expect(wrapper.emitted('update')).toEqual([['model', 'claude-opus-4-6']])
  })

  it('clears an effort the default model does not support', async () => {
    const wrapper = render({ model: 'claude-opus-5', effort: 'high' })
    await wrapper.get('[data-param="model"] select').setValue('')
    expect(wrapper.emitted('update')).toEqual([
      ['model', ''],
      ['effort', null],
    ])
  })
})
