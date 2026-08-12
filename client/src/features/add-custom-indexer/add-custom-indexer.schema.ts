import * as z from 'zod'

export const addCustomIndexerSchema = z.object({
  name: z.string().trim().nonempty('A név kitöltése kötelező'),
  torznabUrl: z
    .string()
    .trim()
    .nonempty('A Torznab URL kitöltése kötelező')
    .refine((value) => {
      try {
        const url = new URL(value)
        return url.protocol === 'http:' || url.protocol === 'https:'
      } catch {
        return false
      }
    }, 'Érvénytelen Torznab URL'),
  apiKey: z.string().trim().nonempty('Az API kulcs kitöltése kötelező'),
  searchMode: z.enum(['auto', 'text']),
})

export type AddCustomIndexerFormValues = z.infer<typeof addCustomIndexerSchema>
