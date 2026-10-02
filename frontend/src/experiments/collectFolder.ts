/**
 * The "Sammeln in" folder of an experiment page: where "Sammeln" files new
 * outputs. Remembered per experiment in this browser, like the page's other
 * conveniences, so one person's choice never changes a colleague's.
 */
const PREFIX = 'kalliope-collect-folder:'

export function readCollectFolder(experimentKey: string): string | null {
  try {
    return localStorage.getItem(PREFIX + experimentKey) || null
  } catch {
    return null
  }
}

export function writeCollectFolder(experimentKey: string, folderId: string | null): void {
  try {
    if (folderId) localStorage.setItem(PREFIX + experimentKey, folderId)
    else localStorage.removeItem(PREFIX + experimentKey)
  } catch {
    /* storage can be unavailable; "Sammeln" then files under "Ohne Ordner" */
  }
}
