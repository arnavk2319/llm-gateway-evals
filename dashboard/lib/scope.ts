/** Whose data a request may see. `userId: null` means everything (admins only). The gateway enforces it. */
export type Scope = { userId: string | null };
