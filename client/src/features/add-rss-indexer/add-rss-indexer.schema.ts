import * as z from 'zod'

export const CUSTOM_SOURCE_VALUE = '__custom__'

export const addRssIndexerSchema = z
  .object({
    name: z.string().trim().nonempty('A név kitöltése kötelező'),
    // A preset id-ja, vagy a CUSTOM_SOURCE_VALUE egyéni URL esetén
    source: z.string().nonempty('Válassz forrást'),
    customUrl: z.string().trim(),
  })
  .refine(
    (value) => {
      if (value.source !== CUSTOM_SOURCE_VALUE) return true
      const url = value.customUrl
      try {
        const parsed = new URL(url)
        return (
          (parsed.protocol === 'http:' || parsed.protocol === 'https:') &&
          url.includes('{query}')
        )
      } catch {
        return false
      }
    },
    {
      message: 'Adj meg egy érvényes RSS URL-t a {query} helykitöltővel',
      path: ['customUrl'],
    },
  )

export type AddRssIndexerFormValues = z.infer<typeof addRssIndexerSchema>
