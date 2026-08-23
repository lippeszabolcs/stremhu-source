import { useSuspenseQuery } from '@tanstack/react-query'
import { toast } from 'sonner'

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/shared/components/ui/card'
import { Label } from '@/shared/components/ui/label'
import { Switch } from '@/shared/components/ui/switch'
import { parseApiError } from '@/shared/lib/utils'
import {
  getSystemSettings,
  useSystemSettingsUpdate,
} from '@/shared/queries/system'

export function AdultFilter() {
  const { data: systemSetting } = useSuspenseQuery(getSystemSettings)

  const { mutateAsync: updateSetting } = useSystemSettingsUpdate()

  const handleChange = async (checked: boolean) => {
    try {
      await updateSetting({ filterAdult: checked })
    } catch (error) {
      const message = parseApiError(error)
      toast.error(message)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Tartalom szűrés</CardTitle>
        <CardDescription>
          A Stremio keresőjéből érkező szöveges keresések találatainak szűrése.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-1">
          <Label htmlFor="filterAdult" className="flex items-start gap-3">
            <p className="flex-1 text-sm leading-none font-medium">
              XXX kiszűrése
            </p>
            <Switch
              id="filterAdult"
              checked={systemSetting.filterAdult}
              onCheckedChange={handleChange}
            />
          </Label>
          <p className="text-muted-foreground text-sm">
            A felnőtt (pornó) találatok kiszűrése a keresésből minden
            felhasználónál. Az nCore xxx kategóriái és a felnőtt tartalomra
            utaló nevű torrentek nem jelennek meg.
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
