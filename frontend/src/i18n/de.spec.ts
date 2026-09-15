import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import de from './de'
import { tt } from './index'

/**
 * §1.4: UI strings live in `src/i18n/` and must not be inlined in components.
 * A template literal in a `.vue` file is not automatically a violation — class
 * names, CSS and code all contain strings — so this checks the specific thing
 * that goes wrong in practice: a hard-coded German word in markup.
 */
function vueFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const path = join(directory, entry)
    if (statSync(path).isDirectory()) return vueFiles(path)
    return path.endsWith('.vue') ? [path] : []
  })
}

describe('German UI strings', () => {
  it('covers every screen the console has', () => {
    for (const key of [
      'app',
      'nav',
      'common',
      'login',
      'documents',
      'folders',
      'report',
      'structure',
      'run',
      'graph',
      'gate',
      'review',
      'exports',
      'errors',
      'pipelines',
      'formats',
      'nodeCatalogue',
      'bench',
      'notes',
    ]) {
      expect(de).toHaveProperty(key)
    }
  })

  it('labels every state a node can be in', () => {
    for (const status of ['pending', 'running', 'cached', 'ok', 'failed', 'blocked', 'paused']) {
      expect(de.graph.status).toHaveProperty(status)
    }
  })

  it('labels every gate the backend can report', () => {
    for (const id of ['G0', 'G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G8']) {
      expect(tt(`gate.${id}`)).not.toBe(`gate.${id}`)
    }
  })

  it('labels every run and parse status', () => {
    for (const status of [
      'queued',
      'running',
      'paused',
      'completed',
      'failed',
      'in_review',
      'reviewed',
    ]) {
      expect(de.run.status).toHaveProperty(status)
    }
    for (const status of ['pending', 'parsing', 'parsed', 'failed']) {
      expect(de.documents.parseStatus).toHaveProperty(status)
    }
  })

  it('falls back to the path when a key is missing', () => {
    expect(tt('does.not.exist')).toBe('does.not.exist')
    expect(tt('does.not.exist', 'fallback')).toBe('fallback')
  })

  it('keeps user-visible German out of component markup', () => {
    const offenders: string[] = []
    // Words that only ever appear as UI copy, never as an identifier.
    const german = /\b(Dokument|Lauf|Skript|Quelle|Abbrechen|Speichern|Anmelden|Prüfung)\b/

    for (const file of vueFiles(join(import.meta.dirname, '..'))) {
      const source = readFileSync(file, 'utf8')
      const template = source.split('<template>')[1]?.split('</template>')[0] ?? ''
      for (const line of template.split('\n')) {
        // Bindings and interpolations resolve through `t`, so only literal
        // text nodes and attribute values matter here.
        const stripped = line.replace(/\{\{[^}]*\}\}/g, '').replace(/:[\w-]+="[^"]*"/g, '')
        if (german.test(stripped)) offenders.push(`${file}: ${line.trim()}`)
      }
    }

    expect(offenders).toEqual([])
  })
})
