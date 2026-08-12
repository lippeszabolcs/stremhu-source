import { describe, expect, it } from 'vitest'

import {
  CUSTOM_SOURCE_VALUE,
  addRssIndexerSchema,
} from './add-rss-indexer.schema'

describe('addRssIndexerSchema', () => {
  it('preset forrás érvényes', () => {
    const result = addRssIndexerSchema.safeParse({
      name: 'Nyaa',
      source: 'nyaa-anime',
      customUrl: '',
    })
    expect(result.success).toBe(true)
  })

  it('egyéni URL {query}-vel érvényes', () => {
    const result = addRssIndexerSchema.safeParse({
      name: 'Saját',
      source: CUSTOM_SOURCE_VALUE,
      customUrl: 'https://example.org/rss?q={query}',
    })
    expect(result.success).toBe(true)
  })

  it('üres név elbukik', () => {
    const result = addRssIndexerSchema.safeParse({
      name: '   ',
      source: 'nyaa-anime',
    })
    expect(result.success).toBe(false)
  })

  it('egyéni URL {query} nélkül elbukik', () => {
    const result = addRssIndexerSchema.safeParse({
      name: 'Saját',
      source: CUSTOM_SOURCE_VALUE,
      customUrl: 'https://example.org/rss?q=valami',
    })
    expect(result.success).toBe(false)
  })

  it('egyéni URL nem-http sémával elbukik', () => {
    const result = addRssIndexerSchema.safeParse({
      name: 'Saját',
      source: CUSTOM_SOURCE_VALUE,
      customUrl: 'ftp://example.org/rss?q={query}',
    })
    expect(result.success).toBe(false)
  })

  it('presetnél az üres customUrl rendben', () => {
    const result = addRssIndexerSchema.safeParse({
      name: 'Nyaa',
      source: 'nyaa-all',
      customUrl: '',
    })
    expect(result.success).toBe(true)
  })
})
