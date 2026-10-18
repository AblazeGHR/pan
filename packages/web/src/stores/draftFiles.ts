/** Browser-owned bytes for uploads which have no server identity yet.
 * File objects use IndexedDB structured cloning, never JSON serialization.
 * Keys include the Session and occurrence so neither switching nor duplicate
 * occurrences can transfer bytes to another draft. */
const files = new Map<string, File>();
const pending = new Map<string, Promise<void>>();
let database: Promise<IDBDatabase> | undefined;
function db() {
  return database ??= new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('pan-composer-files', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('files');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => { database = undefined; reject(request.error); };
  });
}
function writeFile(key: string, file: File) {
  const write = db().then((database) => new Promise<void>((resolve, reject) => {
    const transaction = database.transaction('files', 'readwrite');
    transaction.objectStore('files').put(file, key);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error || new Error('Draft file write aborted'));
  }));
  // Keep rejection observable by the draft pipeline without an unhandled rejection.
  void write.catch(() => {});
  pending.set(key, write);
  return write;
}
export function rememberDraftFile(key: string, file: File) {
  if (files.get(key) === file) return;
  files.set(key, file);
  void writeFile(key, file);
}
export async function ensureDraftFiles(keys: string[]) {
  await Promise.all(keys.map(async (key) => {
    try { await pending.get(key); }
    catch (error) {
      const file = files.get(key);
      if (!file) throw error;
      // One retry here; persistent failure uses the draft store's bounded
      // retry budget and remains visibly unsaved instead of sending a bare key.
      database = undefined;
      await writeFile(key, file);
    }
  }));
}
export async function loadDraftFile(key: string) {
  if (files.has(key)) return files.get(key);
  const database = await db();
  const file = await new Promise<File | undefined>((resolve, reject) => {
    const request = database.transaction('files').objectStore('files').get(key);
    request.onsuccess = () => resolve(request.result instanceof File ? request.result : undefined);
    request.onerror = () => reject(request.error);
  });
  if (file) files.set(key, file);
  return file;
}
export function draftFile(key: string) { return files.get(key); }

/** Run only after an acknowledged draft write. Never delete bytes still named
 * by that persisted snapshot; an upload failure or session switch isn't a clear. */
export async function pruneDraftFiles(sessionId: string, keep: () => string[]) {
  const database = await db();
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction('files', 'readwrite');
    const store = transaction.objectStore('files');
    const request = store.openKeyCursor();
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) return;
      const key = String(cursor.key);
      if (key.startsWith(`${sessionId}:`) && !keep().includes(key)) {
        store.delete(cursor.key); files.delete(key); pending.delete(key);
      }
      cursor.continue();
    };
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}
