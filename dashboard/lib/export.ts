export type ExportFormat = "csv" | "jsonl" | "json";

export const parseFormat = (v: string | null): ExportFormat =>
  v === "jsonl" || v === "json" ? v : "csv";

const csvCell = (v: unknown): string => {
  if (v === null || v === undefined) return "";
  const s = typeof v === "object" ? JSON.stringify(v) : String(v);
  // Quote when needed; neutralize spreadsheet formula injection from log text.
  const safe = /^[=+\-@\t\r]/.test(s) ? `'${s}` : s;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
};

export function toCsv(columns: string[], rows: Record<string, unknown>[]): string {
  const lines = [columns.join(",")];
  for (const r of rows) lines.push(columns.map((c) => csvCell(r[c])).join(","));
  return lines.join("\n") + "\n";
}

export function exportResponse(
  name: string,
  format: ExportFormat,
  columns: string[],
  rows: Record<string, unknown>[],
): Response {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const headers = (type: string, ext: string) => ({
    "content-type": `${type}; charset=utf-8`,
    "content-disposition": `attachment; filename="${name}-${stamp}.${ext}"`,
    "cache-control": "no-store",
  });
  if (format === "json") return new Response(JSON.stringify(rows, null, 2), { headers: headers("application/json", "json") });
  if (format === "jsonl")
    return new Response(rows.map((r) => JSON.stringify(r)).join("\n") + "\n", {
      headers: headers("application/x-ndjson", "jsonl"),
    });
  return new Response(toCsv(columns, rows), { headers: headers("text/csv", "csv") });
}
