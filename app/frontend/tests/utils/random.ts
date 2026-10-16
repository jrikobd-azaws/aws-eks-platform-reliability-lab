const randomHex = (length: number): string =>
  crypto.randomUUID().replace(/-/g, "").slice(0, length)

export const randomEmail = () =>
  `test_${randomHex(12)}@example.com`

export const randomTeamName = () =>
  `Team ${randomHex(12)}`

export const randomPassword = () => `${randomHex(20)}`

export const slugify = (text: string) =>
  text
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^\w-]+/g, "")

export const randomItemTitle = () =>
  `Item ${randomHex(12)}`

export const randomItemDescription = () =>
  `Description ${randomHex(12)}`
