/** Client-side checks are limited to extension and size; every business rule is validated by the server. */
export const ACCEPTED_EXT = [".xlsx", ".csv"];

export function checkFile(file: File, maxMb: number): string | null {
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXT.some((e) => name.endsWith(e))) return `Unsupported file type. Use ${ACCEPTED_EXT.join(" or ")}.`;
  if (file.size === 0) return "The file is empty.";
  if (file.size > maxMb * 1024 * 1024) return `File is larger than ${maxMb} MB.`;
  return null;
}
