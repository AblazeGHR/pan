/** Let the browser produce a ClipboardEvent; xterm.paste adds mode-aware framing.
 * Sending Ctrl-V as terminal data cancels the browser's default paste action.
 * Ctrl-C/D and all non-clipboard terminal keys retain xterm's normal semantics.
 */
export function terminalKeyHandler(event: KeyboardEvent): boolean {
  return !(event.ctrlKey && !event.altKey && !event.metaKey && event.key.toLowerCase() === 'v');
}
