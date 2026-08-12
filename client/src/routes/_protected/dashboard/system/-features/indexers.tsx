import { useSuspenseQueries } from '@tanstack/react-query'
import { LogInIcon, RssIcon } from 'lucide-react'
import type { MouseEventHandler } from 'react'

import { useDialogs } from '@/routes/-features/dialogs/dialogs-store'
import { Button } from '@/shared/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/shared/components/ui/card'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from '@/shared/components/ui/empty'
import { Separator } from '@/shared/components/ui/separator'
import { getIndexerDefinitions, getIndexers } from '@/shared/queries/indexers'

import { IndexerItem } from '../-components/indexer-item'

export function Indexers() {
  const [{ data: indexers }, { data: indexerDefinitions }] = useSuspenseQueries(
    {
      queries: [getIndexers, getIndexerDefinitions],
    },
  )

  const dialogs = useDialogs()

  // A "Bejelentkezés" gomb csak a beépített oldalakra vonatkozik; egyéni
  // (RSS) indexerből akárhány felvehető
  const builtinDefinitions = indexerDefinitions.filter(
    (definition) => definition.kind === 'builtin',
  )
  const builtinIndexers = indexers.filter(
    (indexer) => indexer.indexerDefinition.kind === 'builtin',
  )
  const renderLogin = builtinIndexers.length < builtinDefinitions.length

  const handleLogin: MouseEventHandler<HTMLButtonElement> = (e) => {
    e.preventDefault()
    e.stopPropagation()
    dialogs.openDialog({
      type: 'ADD_INDEXER',
      options: {
        activeIndexerIds: indexers.map((indexer) => indexer.indexerId),
      },
    })
  }

  const handleAddRss: MouseEventHandler<HTMLButtonElement> = (e) => {
    e.preventDefault()
    e.stopPropagation()
    dialogs.openDialog({
      type: 'ADD_RSS_INDEXER',
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Torrent oldalak</CardTitle>
        <CardDescription>
          Kezeld a torrent oldal bejelentkezéseidet és konfiguráld őket.
        </CardDescription>
        <CardAction className="flex gap-2">
          <Button
            size="icon-sm"
            variant="outline"
            className="rounded-full"
            title="Beépített tracker (RSS, pl. Nyaa.si)"
            onClick={handleAddRss}
          >
            <RssIcon />
          </Button>
          {renderLogin && (
            <Button
              size="icon-sm"
              className="rounded-full"
              onClick={handleLogin}
            >
              <LogInIcon />
            </Button>
          )}
        </CardAction>
      </CardHeader>
      <Separator />
      <CardContent className="grid gap-4">
        {indexers.map((indexer) => (
          <IndexerItem key={indexer.indexerId} indexer={indexer} />
        ))}
        {indexers.length === 0 && (
          <Empty className="p-2 md:p-2">
            <EmptyHeader>
              <EmptyTitle>Jelentkezz be!</EmptyTitle>
              <EmptyDescription>
                A StremHU Source használatához, be kell jelentkezned legalább
                egy torrent oldalra!
              </EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <div className="flex flex-wrap justify-center gap-2">
                <Button size="sm" onClick={handleLogin}>
                  <LogInIcon />
                  Bejelentkezés
                </Button>
                <Button size="sm" variant="outline" onClick={handleAddRss}>
                  <RssIcon />
                  Beépített tracker
                </Button>
              </div>
            </EmptyContent>
          </Empty>
        )}
      </CardContent>
    </Card>
  )
}
