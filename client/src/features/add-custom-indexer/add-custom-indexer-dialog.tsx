import type { MouseEventHandler, SubmitEventHandler } from 'react'
import { toast } from 'sonner'

import type { OpenedDialog } from '@/routes/-features/dialogs/dialogs-store'
import { useDialogsStore } from '@/routes/-features/dialogs/dialogs-store'
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogScrollContent,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Field, FieldLabel } from '@/shared/components/ui/field'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { useAppForm } from '@/shared/contexts/form-context'
import { parseApiError } from '@/shared/lib/utils'
import { useCustomIndexerCreate } from '@/shared/queries/indexers'

import { addCustomIndexerSchema } from './add-custom-indexer.schema'
import type { AddCustomIndexerDialog } from './add-custom-indexer.types'

const SEARCH_MODE_OPTIONS = [
  { value: 'auto', label: 'Automatikus (IMDb)' },
  { value: 'text', label: 'Szöveges keresés' },
] as const

export function AddCustomIndexerDialog(
  dialog: OpenedDialog & AddCustomIndexerDialog,
) {
  const dialogsStore = useDialogsStore()

  const { mutateAsync: createCustomIndexer } = useCustomIndexerCreate()

  const form = useAppForm({
    defaultValues: {
      name: '',
      torznabUrl: '',
      apiKey: '',
      searchMode: 'auto' as 'auto' | 'text',
    },
    validators: {
      onChange: addCustomIndexerSchema,
    },
    onSubmit: async ({ value }) => {
      try {
        await createCustomIndexer(value)
        dialogsStore.closeDialog(dialog.id)
      } catch (error) {
        const message = parseApiError(error)
        toast.error(message)
      }
    },
  })

  const handleSubmit: SubmitEventHandler<HTMLFormElement> = async (e) => {
    e.preventDefault()
    e.stopPropagation()
    await form.handleSubmit()
  }

  const handleClose: MouseEventHandler<HTMLButtonElement> = (e) => {
    e.preventDefault()
    e.stopPropagation()
    dialogsStore.closeDialog(dialog.id)
  }

  return (
    <Dialog open={dialog.open}>
      <DialogScrollContent
        className="md:max-w-md"
        onEscapeKeyDown={() => dialogsStore.closeDialog(dialog.id)}
      >
        <form.AppForm>
          <form className="grid gap-4" onSubmit={handleSubmit}>
            <DialogHeader>
              <DialogTitle>Egyéni indexer hozzáadása</DialogTitle>
              <DialogDescription>
                Adj hozzá tetszőleges trackert egy Torznab kompatibilis proxy-n
                (Prowlarr / Jackett) keresztül. A Torznab URL az adott indexer
                feed címe (pl. http://prowlarr:9696/1/api).
              </DialogDescription>
            </DialogHeader>
            <form.AppField
              name="name"
              children={(field) => <field.AppTextField label="Név" />}
            />
            <form.AppField
              name="torznabUrl"
              children={(field) => <field.AppTextField label="Torznab URL" />}
            />
            <form.AppField
              name="apiKey"
              children={(field) => (
                <field.AppTextField label="API kulcs" type="password" />
              )}
            />
            <form.Field name="searchMode">
              {(field) => (
                <Field>
                  <FieldLabel htmlFor={field.name}>Keresési mód</FieldLabel>
                  <Select
                    value={field.state.value}
                    name={field.name}
                    onValueChange={(value) =>
                      field.handleChange(value as 'auto' | 'text')
                    }
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SEARCH_MODE_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
              )}
            </form.Field>
            <DialogFooter>
              <form.SubscribeButton
                variant="outline"
                type="button"
                onClick={handleClose}
              >
                Mégsem
              </form.SubscribeButton>
              <form.SubscribeButton type="submit">
                Hozzáadás
              </form.SubscribeButton>
            </DialogFooter>
          </form>
        </form.AppForm>
      </DialogScrollContent>
    </Dialog>
  )
}
