export const APP_PREFERENCE_KEYS = {
  dismissed: 'leontv.dismissed-completed-tasks',
  sidebar: 'leontv.sidebar-open',
} as const;

// Explicit compatibility identifiers for existing users; never delete old data.
const LEGACY_KEYS = {
  dismissed: 'xiaoyunque.dismissed-completed-tasks',
  sidebar: 'xiaoyunque.sidebar-open',
} as const;

export function readAppPreference(storage: Pick<Storage, 'getItem'>, preference: keyof typeof APP_PREFERENCE_KEYS): string | null {
  return storage.getItem(APP_PREFERENCE_KEYS[preference]) ?? storage.getItem(LEGACY_KEYS[preference]);
}

export function migrateAppPreferences(storage: Pick<Storage, 'getItem' | 'setItem'>): void {
  for (const preference of Object.keys(APP_PREFERENCE_KEYS) as Array<keyof typeof APP_PREFERENCE_KEYS>) {
    try {
      if (storage.getItem(APP_PREFERENCE_KEYS[preference]) !== null) continue;
      const previous = storage.getItem(LEGACY_KEYS[preference]);
      if (previous !== null) storage.setItem(APP_PREFERENCE_KEYS[preference], previous);
    } catch { /* Read compatibility still works if writes are unavailable. */ }
  }
}
