import { ApiError } from '@/api/client'
import { t } from '@/i18n'

export const MIN_PASSWORD_LENGTH = 10

// No 0/O, 1/l/I: the password is read aloud or typed off a screen.
const ALPHABET = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_'

/** A random password from the browser's CSPRNG, well above the minimum length. */
export function generatePassword(length = 20): string {
  const bytes = new Uint32Array(length)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (value) => ALPHABET[value % ALPHABET.length]).join('')
}

const BY_TITLE: Record<string, string> = {
  'Email in use': t.settings.errors.emailInUse,
  'Invalid email': t.settings.errors.invalidEmail,
  'Name required': t.settings.errors.nameRequired,
  'Password too short': t.settings.errors.passwordShort,
  'Wrong password': t.settings.errors.wrongPassword,
  'Too many attempts': t.settings.errors.tooMany,
  'Cannot remove yourself': t.settings.errors.removeSelf,
  'Last member': t.settings.errors.lastMember,
  'No such member': t.settings.errors.notFound,
  'Use your profile': t.settings.ownPasswordHint,
}

/** German text for a refusal from the account endpoints. */
export function accountError(exc: unknown): string {
  if (exc instanceof ApiError) return BY_TITLE[exc.title] ?? exc.detail ?? t.errors.generic
  return t.errors.generic
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
