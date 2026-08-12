import { useSuspenseQueries } from '@tanstack/react-query'
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
import { getRssPresets, useRssIndexerCreate } from '@/shared/queries/indexers'

import {
  CUSTOM_SOURCE_VALUE,
  addRssIndexerSchema,
} from './add-rss-indexer.schema'
import type { AddRssIndexerDialog } from './add-rss-indexer.types'

export function AddRssIndexerDialog(
  dialog: OpenedDialog & AddRssIndexerDialog,
) {
  const [{ data: presets }] = useSuspenseQueries({
    queries: [getRssPresets],
  })

  const dialogsStore = useDialogsStore()

  const { mutateAsync: createRssIndexer } = useRssIndexerCreate()

  const form = useAppForm({
    defaultValues: {
      name: '',
      source: presets[0]?.id ?? CUSTOM_SOURCE_VALUE,
      customUrl: '',
    },
    validators: {
      onChange: addRssIndexerSchema,
    },
    onSubmit: async ({ value }) => {
      try {
        const isCustom = value.source === CUSTOM_SOURCE_VALUE
        await createRssIndexer({
          name: value.name,
          presetId: isCustom ? undefined : value.source,
          customUrl: isCustom ? value.customUrl : undefined,
        })
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
              <DialogTitle>Beépített tracker hozzáadása</DialogTitle>
              <DialogDescription>
                Publikus tracker közvetlen RSS-keresése — nem kell hozzá külön
                szoftver (Prowlarr). Válassz egy kész forrást, vagy adj meg egy
                egyéni RSS keresŐ-URL-t.
              </DialogDescription>
            </DialogHeader>
            <form.AppField
              name="name"
              children={(field) => <field.AppTextField label="Név" />}
            />
            <form.Field name="source">
              {(field) => (
                <Field>
                  <FieldLabel htmlFor={field.name}>Forrás</FieldLabel>
                  <Select
                    value={field.state.value}
                    name={field.name}
                    onValueChange={(value) => field.handleChange(value)}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {presets.map((preset) => (
                        <SelectItem key={preset.id} value={preset.id}>
                          {preset.name}
                        </SelectItem>
                      ))}
                      <SelectItem value={CUSTOM_SOURCE_VALUE}>
                        Egyéni RSS URL…
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </Field>
              )}
            </form.Field>
            <form.Subscribe selector={(state) => state.values.source}>
              {(source) =>
                source === CUSTOM_SOURCE_VALUE ? (
                  <form.AppField
                    name="customUrl"
                    children={(field) => (
                      <field.AppTextField label="RSS keresŐ-URL ({query} helykitöltővel)" />
                    )}
                  />
                ) : null
              }
            </form.Subscribe>
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
