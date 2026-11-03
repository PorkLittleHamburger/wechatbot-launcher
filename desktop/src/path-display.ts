/** UI text only. Keep the original path for native commands and filesystem I/O. */
export function displayPath(path: string | null | undefined): string {
  if (!path) return '';
  let readable: string;
  if (/^\\\\\?\\[a-z]:\\/i.test(path)) readable = path.slice(4);
  else if (/^\\\\\?\\UNC\\/i.test(path)) readable = `\\\\${path.slice(8)}`;
  else return path;

  // These names rely on verbatim Windows semantics; hiding their prefix could
  // misleadingly describe a different directory when the text is copied.
  const special = readable.split('\\').some(part => part.endsWith('.') || part.endsWith(' ')
    || /^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(\.|$)/i.test(part));
  return special ? path : readable;
}
