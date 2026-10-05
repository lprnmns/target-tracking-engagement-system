/**
 * Görsel hedef adlandırması.
 *
 * Perception/tracking sözleşmeleri ASCII makine kimliklerini kullanır. Bu
 * yardımcı yalnızca operatöre gösterilen adları üretir; böylece kayıt,
 * tracking ve CommandGateway kimlikleri değişmeden Türkçe bir arayüz elde
 * ederiz.
 */

const CLASS_ALIASES: Record<string, string> = {
  f_16: 'f16',
  'f-16': 'f16',
  savas_ucagi: 'f16',
  f16_dusman: 'f16',
  uav: 'mini_micro_uav',
  drone: 'mini_micro_uav',
  mini_micro_iha: 'mini_micro_uav',
  mini_mikro_iha: 'mini_micro_uav',
  balistik_fuze: 'ballistic_missile',
  enemy_f16: 'f16',
  f16_enemy: 'f16',
  enemy_helicopter: 'helicopter',
  helicopter_enemy: 'helicopter',
  enemy_ballistic_missile: 'ballistic_missile',
  ballistic_missile_enemy: 'ballistic_missile',
  enemy_mini_micro_uav: 'mini_micro_uav',
  mini_micro_uav_enemy: 'mini_micro_uav',
  balloon: 'balloon',
  balon: 'balloon',
}

const CLASS_SLUGS: Record<string, string> = {
  f16: 'savas_ucagi',
  helicopter: 'helikopter',
  ballistic_missile: 'balistik_fuze',
  mini_micro_uav: 'mini_mikro_iha',
  balloon: 'balon',
}

const CLASS_LABELS: Record<string, string> = {
  f16: 'Savaş Uçağı',
  helicopter: 'Helikopter',
  ballistic_missile: 'Balistik Füze',
  mini_micro_uav: 'Mini/Mikro İHA',
  balloon: 'Balon',
}

export function targetClassToken(value: string | null | undefined): string {
  const raw = String(value ?? 'target').trim().toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
  const token = raw.replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'target'
  return CLASS_ALIASES[token] ?? token
}

export function targetClassSlug(value: string | null | undefined): string {
  const token = targetClassToken(value)
  return CLASS_SLUGS[token] ?? token
}

export function targetClassLabel(value: string | null | undefined): string {
  const token = targetClassToken(value)
  return CLASS_LABELS[token] ?? token.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

export function targetTeamToken(value: string | null | undefined): string {
  const token = String(value ?? 'unknown').trim().toLowerCase()
  return token === 'enemy' || token === 'dusman' ? 'dusman' : token === 'friend' || token === 'dost' ? 'dost' : 'bilinmeyen'
}

export function targetTeamLabel(value: string | null | undefined): string {
  return { dusman: 'Düşman', dost: 'Dost', bilinmeyen: 'Bilinmeyen' }[targetTeamToken(value)] ?? 'Bilinmeyen'
}

export function targetSequenceFromId(value: string | null | undefined, fallback = 1): number {
  const match = String(value ?? '').match(/_(\d+)(?:_hedefi)?(?:\s*—\s*İmha Edildi)?$/i)
  const sequence = match ? Number(match[1]) : fallback
  return Number.isFinite(sequence) && sequence > 0 ? sequence : fallback
}

export function targetDisplayName(options: {
  className?: string | null
  team?: string | null
  sequence?: number
  balloon?: boolean
  standaloneBalloon?: boolean
  destroyed?: boolean
}): string {
  const sequence = Math.max(1, Math.floor(options.sequence ?? 1))
  const balloon = options.balloon === true
  const base = options.standaloneBalloon
    ? `balon_${sequence}_hedefi`
    : balloon
      ? targetTeamToken(options.team) === 'dost'
        ? `${targetClassSlug(options.className)}_dost_${sequence}_balon`
        : `${targetClassSlug(options.className)}_${sequence}_hedefi`
      : `${targetClassSlug(options.className)}_${targetTeamToken(options.team)}_${sequence}`
  return options.destroyed ? `${base} — İmha Edildi` : base
}

export function targetDisplayDescription(options: {
  className?: string | null
  team?: string | null
  balloon?: boolean
  standaloneBalloon?: boolean
}): string {
  if (options.standaloneBalloon) return 'Hedef Balonu'
  if (options.balloon) {
    return targetTeamToken(options.team) === 'dost'
      ? `${targetClassLabel(options.className)} · Dost Balonu`
      : `${targetClassLabel(options.className)} · Hedef Balonu`
  }
  return `${targetClassLabel(options.className)} · ${targetTeamLabel(options.team)}`
}
