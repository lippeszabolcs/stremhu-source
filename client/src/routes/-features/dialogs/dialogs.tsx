import type { ComponentType } from 'react'
import { Fragment } from 'react/jsx-runtime'

import { AddCustomIndexerDialog } from '@/features/add-custom-indexer/add-custom-indexer-dialog'
import { AddIndexerDialog } from '@/features/add-indexer/add-indexer-dialog'
import { AddRssIndexerDialog } from '@/features/add-rss-indexer/add-rss-indexer-dialog'
import { AddUserDialog } from '@/features/add-user/add-user-dialog'
import { ChangePasswordDialog } from '@/features/change-password/change-password-dialog'
import { ChangeUsernameDialog } from '@/features/change-username/change-username-dialog'
import { ConfirmDialog } from '@/features/confirm/confirm-dialog'
import { EditIndexerDialog } from '@/features/edit-indexer/edit-indexer-dialog'
import type { OpenedDialog } from '@/routes/-features/dialogs/dialogs-store'
import { useDialogsStore } from '@/routes/-features/dialogs/dialogs-store'

type DialogComponentMap = {
  [K in OpenedDialog['type']]: ComponentType<Extract<OpenedDialog, { type: K }>>
}

const dialogComponents = {
  CONFIRM: ConfirmDialog,
  ADD_INDEXER: AddIndexerDialog,
  ADD_CUSTOM_INDEXER: AddCustomIndexerDialog,
  ADD_RSS_INDEXER: AddRssIndexerDialog,
  CHANGE_USERNAME: ChangeUsernameDialog,
  CHANGE_PASSWORD: ChangePasswordDialog,
  ADD_USER: AddUserDialog,
  EDIT_INDEXER: EditIndexerDialog,
} satisfies DialogComponentMap

export function Dialogs() {
  const { dialogs } = useDialogsStore()

  return dialogs.map((dialog) => {
    const DialogComponent = dialogComponents[dialog.type] as ComponentType<
      typeof dialog
    >

    return (
      <Fragment key={dialog.id}>
        <DialogComponent {...dialog} />
      </Fragment>
    )
  })
}
