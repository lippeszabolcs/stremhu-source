import { describe, expect, it } from 'vitest'

import { addCustomIndexerSchema } from './add-custom-indexer.schema'

const validPayload = {
  name: 'Nyaa',
  torznabUrl: 'http://prowlarr:9696/1/api',
  apiKey: 'abc123',
  searchMode: 'auto',
}

describe('addCustomIndexerSchema', () => {
  it('érvényes payload átmegy', () => {
    const result = addCustomIndexerSchema.safeParse(validPayload)
    expect(result.success).toBe(true)
  })

  it('https URL és text mód is érvényes', () => {
    const result = addCustomIndexerSchema.safeParse({
      ...validPayload,
      torznabUrl:
        'https://jackett.example.com/api/v2.0/indexers/nyaa/results/torznab/',
      searchMode: 'text',
    })
    expect(result.success).toBe(true)
  })

  it('üres név elbukik', () => {
    const result = addCustomIndexerSchema.safeParse({
      ...validPayload,
      name: '   ',
    })
    expect(result.success).toBe(false)
  })

  it('nem URL torznabUrl elbukik', () => {
    const result = addCustomIndexerSchema.safeParse({
      ...validPayload,
      torznabUrl: 'nem-url',
    })
    expect(result.success).toBe(false)
  })

  it('nem http(s) sémájú URL elbukik', () => {
    const result = addCustomIndexerSchema.safeParse({
      ...validPayload,
      torznabUrl: 'ftp://prowlarr:9696/1/api',
    })
    expect(result.success).toBe(false)
  })

  it('üres API kulcs elbukik', () => {
    const result = addCustomIndexerSchema.safeParse({
      ...validPayload,
      apiKey: '',
    })
    expect(result.success).toBe(false)
  })

  it('ismeretlen keresési mód elbukik', () => {
    const result = addCustomIndexerSchema.safeParse({
      ...validPayload,
      searchMode: 'fuzzy',
    })
    expect(result.success).toBe(false)
  })
})
