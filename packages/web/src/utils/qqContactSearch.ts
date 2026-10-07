/** Local search only; never changes the caller's full target identity. */
export function matchesQqContact(query: string, ...fields: (string | undefined)[]): boolean {
  const normalized = query.trim().toLowerCase();
  return !normalized || fields.some((field) => field?.toLowerCase().includes(normalized));
}
