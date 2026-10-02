import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import ArtifactView from './ArtifactView.vue'
import {
  loadArtifactMode,
  pickRenderer,
  readBeat,
  readOutline,
  readScript,
  setArtifactMode,
} from './artifactView'

const beat = {
  id: 'b2',
  title: 'Was die Reaktion braucht',
  block_ids: ['b18', 'b19'],
  word_budget: 220,
  goal_id: 'g1',
  summary: 'Licht, Wasser und Kohlenstoffdioxid.',
}

const script = {
  segments: [
    {
      id: 's1',
      speaker: 'Moderator',
      text: 'Schauen wir auf ein Blatt.',
      kind: 'pedagogy',
      beat_id: 'b1',
      anchors: [],
    },
    {
      id: 's2',
      speaker: 'Expertin',
      text: 'Chlorophyll nimmt blaues und rotes Licht auf.',
      kind: 'claim',
      beat_id: 'b1',
      anchors: [{ block_id: 'b14', char_start: 42, char_end: 118 }],
    },
  ],
}

const scriptPayload = {
  segments: [
    {
      id: 's2',
      speaker: 'Expertin',
      text: 'Chlorophyll nimmt blaues und rotes Licht auf.',
      kind: 'claim',
      beat_id: 'b1',
      anchors: [
        { document_id: 'doc', parse_version: 1, block_id: 'b14', char_start: 42, char_end: 118 },
      ],
    },
  ],
}

describe('pickRenderer', () => {
  it('names the three models that have a text view', () => {
    expect(pickRenderer('Outline')).toBe('outline')
    expect(pickRenderer('Beat')).toBe('beat')
    expect(pickRenderer('Script')).toBe('script')
  })

  it('returns none for every other model name', () => {
    expect(pickRenderer('Selection')).toBeNull()
    expect(pickRenderer('ParsedDocument')).toBeNull()
    expect(pickRenderer('unknown')).toBeNull()
  })
})

describe('payload shape', () => {
  it('reads a beat, an outline, and a script', () => {
    expect(readBeat(beat)?.block_ids).toEqual(['b18', 'b19'])
    expect(readOutline({ beats: [beat] })?.[0].title).toBe(beat.title)
    expect(readScript(scriptPayload)?.[0].anchors[0].block_id).toBe('b14')
  })

  it('rejects an outline that has no beats array, without keeping a partial beat', () => {
    expect(readOutline({ title: 'kaputt', beats: [{ title: 'halb' }] })).toBeNull()
    expect(readOutline({ title: 'kaputt' })).toBeNull()
  })

  it('rejects a preview string that is not an object', () => {
    expect(readOutline('{ "beats": [{ "title": "abgeschnitten"')).toBeNull()
    expect(readScript('{"segments":')).toBeNull()
  })
})

describe('artifact view preference', () => {
  beforeEach(() => {
    localStorage.clear()
    loadArtifactMode()
  })

  it('stores the last choice under kalliope-artifact-view', () => {
    setArtifactMode('json')
    expect(localStorage.getItem('kalliope-artifact-view')).toBe('json')
    loadArtifactMode()
    setArtifactMode('text')
    expect(localStorage.getItem('kalliope-artifact-view')).toBe('text')
  })
})

describe('ArtifactView', () => {
  beforeEach(() => {
    localStorage.clear()
    loadArtifactMode()
  })

  it('shows a beat title, its word budget, and its passage ids', () => {
    const wrapper = mount(ArtifactView, { props: { model: 'Beat', payload: beat } })
    expect(wrapper.text()).toContain('Was die Reaktion braucht')
    expect(wrapper.text()).toContain('220')
    expect(wrapper.text()).toContain('b18')
    expect(wrapper.text()).toContain('b19')
  })

  it('shows a script speaker, the claim badge, and the block id', () => {
    const wrapper = mount(ArtifactView, { props: { model: 'Script', payload: script } })
    expect(wrapper.text()).toContain('Expertin')
    expect(wrapper.text()).toContain('Aussage')
    expect(wrapper.text()).toContain('Didaktik')
    expect(wrapper.text()).toContain('b14')
    expect(wrapper.find('[data-copy], .jsonbar').exists() || wrapper.text().includes('JSON kopieren')).toBe(
      false,
    )
  })

  it('keeps the copy control off the text view', () => {
    const wrapper = mount(ArtifactView, { props: { model: 'Beat', payload: beat } })
    expect(wrapper.text()).not.toContain('JSON kopieren')
  })

  it('remembers JSON and marks the pressed mode', async () => {
    const wrapper = mount(ArtifactView, { props: { model: 'Beat', payload: beat } })
    const json = wrapper.findAll('button').find((button) => button.text() === 'JSON')
    expect(json).toBeTruthy()
    await json!.trigger('click')
    expect(json!.attributes('aria-pressed')).toBe('true')
    expect(localStorage.getItem('kalliope-artifact-view')).toBe('json')
    expect(wrapper.text()).toContain('JSON kopieren')
    expect(wrapper.find('.beat').exists()).toBe(false)
  })

  it('falls back to JSON when an outline has no beats', () => {
    const wrapper = mount(ArtifactView, {
      props: { model: 'Outline', payload: { title: 'kaputt' } },
    })
    expect(wrapper.text()).toContain('Diese Struktur passt nicht zur Textansicht.')
    expect(wrapper.text()).not.toContain('halb')
    expect(wrapper.find('.beat').exists()).toBe(false)
  })

  it('does not label an empty known-model slot as a bad shape', () => {
    const empties: { preview?: string | null; payload: null }[] = [
      { preview: '', payload: null },
      { preview: null, payload: null },
      { payload: null },
    ]
    for (const model of ['Outline', 'Beat', 'Script']) {
      for (const props of empties) {
        const wrapper = mount(ArtifactView, { props: { model, ...props } })
        expect(wrapper.text()).not.toContain('Diese Struktur passt nicht zur Textansicht.')
      }
    }
  })

  it('does not render a truncated string as a partial tree', () => {
    const wrapper = mount(ArtifactView, {
      props: {
        model: 'Outline',
        preview: '{"beats":[{"title":"abgeschnitten"',
        truncated: true,
      },
    })
    expect(wrapper.text()).toContain('Diese Struktur passt nicht zur Textansicht.')
    expect(wrapper.find('.beat').exists()).toBe(false)
    expect(wrapper.text()).toContain('JSON kopieren')
  })

  it('offers no text view for an unknown model', () => {
    const wrapper = mount(ArtifactView, {
      props: { model: 'ParsedDocument', payload: { pages: [] } },
    })
    expect(wrapper.text()).toContain('Für diesen Typ gibt es keine Textansicht.')
    expect(wrapper.find('[aria-pressed]').exists()).toBe(false)
  })
})
