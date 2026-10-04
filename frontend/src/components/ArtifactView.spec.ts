import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import ArtifactView from './ArtifactView.vue'
import {
  loadArtifactMode,
  pickRenderer,
  readAudioScript,
  readBeat,
  readOutline,
  readSeriesPlan,
  readScript,
  readSelection,
  setArtifactMode,
  tagParts,
} from './artifactView'

const selectionPayload = {
  learning_goals: [
    { id: 'g0', text: 'Die Lichtreaktion erklären', source: 'generated' },
    { id: 'g1', text: 'Den Calvin-Zyklus beschreiben', source: 'generated' },
  ],
  selected_blocks: [
    { block_id: 'b12', salience: 1, reason: null, goal_ids: ['g0', 'g1'] },
    { block_id: 'b14', salience: 1, reason: 'Ort', goal_ids: ['g0'] },
    { block_id: 'b30', salience: 1, reason: null, goal_ids: ['g9'] },
  ],
  rationale: 'Erklärende Passagen gewählt.',
}

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
  it('names the models that have a text view', () => {
    expect(pickRenderer('Outline')).toBe('outline')
    expect(pickRenderer('Beat')).toBe('beat')
    expect(pickRenderer('Script')).toBe('script')
    expect(pickRenderer('Selection')).toBe('selection')
    expect(pickRenderer('AudioScript')).toBe('audio')
    expect(pickRenderer('SeriesPlan')).toBe('series')
  })

  it('returns none for every other model name', () => {
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

  it('reads a selection as goals with the passages that serve them', () => {
    const read = readSelection(selectionPayload)
    expect(read?.goals.map((goal) => goal.block_ids)).toEqual([['b12', 'b14'], ['b12']])
    expect(read?.unassigned).toEqual(['b30'])
    expect(read?.rationale).toBe('Erklärende Passagen gewählt.')
    expect(readSelection({ learning_goals: [] })).toBeNull()
    expect(readSelection({ learning_goals: [{ id: 'g0' }], selected_blocks: [] })).toBeNull()
  })

  it('reads a series plan as episodes with role, goals and passages', () => {
    const read = readSeriesPlan({
      title: 'Eine Serie',
      through_line: 'Vom Grundsatz zur Anwendung.',
      episodes: [
        {
          title: 'Folge 1',
          role: 'Einführung',
          block_ids: ['b12'],
          goals: [{ text: 'Erklären', bloom_level: 'understand' }],
        },
      ],
    })
    expect(read?.title).toBe('Eine Serie')
    expect(read?.throughLine).toBe('Vom Grundsatz zur Anwendung.')
    expect(read?.episodes[0].role).toBe('Einführung')
    expect(read?.episodes[0].goals).toEqual([{ text: 'Erklären', bloom: 'understand' }])
    expect(read?.episodes[0].block_ids).toEqual(['b12'])
    expect(readSeriesPlan({ title: 'kaputt' })).toBeNull()
    expect(readSeriesPlan({ episodes: [{ title: 'halb' }] })).toBeNull()
  })

  it('rejects a preview string that is not an object', () => {
    expect(readOutline('{ "beats": [{ "title": "abgeschnitten"')).toBeNull()
    expect(readScript('{"segments":')).toBeNull()
  })
})

const audioPayload = {
  lines: [
    {
      segment_id: 's1',
      speaker: 'Expertin',
      kind: 'claim',
      text: 'Rund 1 bis 2 % des Lichts.',
      spoken: 'Rund eins bis zwei Prozent des Lichts.',
      tagged: '[thoughtful] Rund eins bis zwei Prozent des Lichts. [short pause]',
      spoken_forms: [{ original: '1 bis 2 %', spoken: 'eins bis zwei Prozent' }],
      tags: ['[thoughtful]', '[short pause]'],
      guard: 'pass',
      problem: null,
    },
  ],
}

describe('audio script', () => {
  it('reads lines with their spoken forms and rejects other shapes', () => {
    const lines = readAudioScript(audioPayload)
    expect(lines?.[0].spoken_forms).toEqual([
      { original: '1 bis 2 %', spoken: 'eins bis zwei Prozent' },
    ])
    expect(readAudioScript({ lines: [{ segment_id: 's1' }] })).toBeNull()
    expect(readAudioScript({ segments: [] })).toBeNull()
  })

  it('splits a tagged line into tags and words', () => {
    expect(tagParts('[curious] Wovon lebt sie? [sighs]')).toEqual([
      { text: '[curious]', tag: true },
      { text: ' Wovon lebt sie? ', tag: false },
      { text: '[sighs]', tag: true },
    ])
    expect(tagParts('ohne Tags')).toEqual([{ text: 'ohne Tags', tag: false }])
  })

  it('shows tags apart from the words and the spoken forms below', () => {
    const wrapper = mount(ArtifactView, {
      props: { model: 'AudioScript', payload: audioPayload, mode: 'text' },
    })
    expect(wrapper.findAll('.audio-tag').map((tag) => tag.text())).toEqual([
      '[thoughtful]',
      '[short pause]',
    ])
    expect(wrapper.text()).toContain('„1 bis 2 %“ → „eins bis zwei Prozent“')
    expect(wrapper.text()).toContain('Wächter: ok')
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

  it('follows a mode set by the parent and hides its own switch', () => {
    setArtifactMode('json')
    const wrapper = mount(ArtifactView, {
      props: { model: 'Outline', payload: { beats: [beat] }, mode: 'text' },
    })
    expect(wrapper.find('.beat').exists()).toBe(true)
    expect(wrapper.find('[aria-pressed]').exists()).toBe(false)
    expect(localStorage.getItem('kalliope-artifact-view')).toBe('json')
  })

  it('renders a selection as goals with their passage ids', () => {
    const wrapper = mount(ArtifactView, {
      props: { model: 'Selection', payload: selectionPayload, mode: 'text' },
    })
    const goals = wrapper.findAll('.beat')
    expect(goals).toHaveLength(2)
    expect(goals[0].text()).toContain('g0')
    expect(goals[0].text()).toContain('Die Lichtreaktion erklären')
    expect(goals[0].findAll('.chip').map((chip) => chip.text())).toEqual(['b12', 'b14'])
    expect(wrapper.text()).toContain('Ohne Lernziel')
    expect(wrapper.text()).toContain('Erklärende Passagen gewählt.')
  })

  it('offers no text view for an unknown model', () => {
    const wrapper = mount(ArtifactView, {
      props: { model: 'ParsedDocument', payload: { pages: [] } },
    })
    expect(wrapper.text()).toContain('Für diesen Typ gibt es keine Textansicht.')
    expect(wrapper.find('[aria-pressed]').exists()).toBe(false)
  })
})
