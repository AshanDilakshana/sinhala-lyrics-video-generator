/**
 * Browser-side project persistence so refreshes never wipe uploaded clips,
 * audio, timeline captions, or styling settings.
 *
 * - Lightweight studio metadata, styles, and timeline lines live in localStorage.
 * - Binary video/audio File objects live in IndexedDB as Blob slices and are
 *   reconstructed back into File instances on restore.
 */

export const PROJECT_STORAGE_KEY = "tiktok_studio_autosave_v1";
const IDB_NAME = "tiktok_studio_media_db";
const IDB_VERSION = 1;
const IDB_STORE = "media_files";
const IDB_KEY_CLIPS = "visual_clips";
const IDB_KEY_SONG = "song_file";

interface StoredFileRecord {
  name: string;
  type: string;
  lastModified: number;
  blob: Blob;
}

function serializeFile(file: File): StoredFileRecord {
  return {
    name: file.name,
    type: file.type || "application/octet-stream",
    lastModified: file.lastModified || Date.now(),
    blob: file.slice(0, file.size, file.type || "application/octet-stream"),
  };
}

function deserializeFile(rec: StoredFileRecord): File | null {
  if (!rec || !rec.blob) return null;
  try {
    return new File([rec.blob], rec.name || "restored-file", {
      type: rec.type || rec.blob.type || "application/octet-stream",
      lastModified: rec.lastModified || Date.now(),
    });
  } catch {
    return null;
  }
}

function openMediaDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof window === "undefined" || !("indexedDB" in window)) {
      reject(new Error("IndexedDB unavailable"));
      return;
    }
    const req = window.indexedDB.open(IDB_NAME, IDB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(IDB_STORE)) {
        db.createObjectStore(IDB_STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error("Failed to open IndexedDB"));
  });
}

export async function saveMediaFilesToIDB(
  visualClips: File[],
  songFile: File | null
): Promise<void> {
  try {
    const db = await openMediaDB();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, "readwrite");
      const store = tx.objectStore(IDB_STORE);
      store.put(visualClips.map(serializeFile), IDB_KEY_CLIPS);
      if (songFile) {
        store.put(serializeFile(songFile), IDB_KEY_SONG);
      } else {
        store.delete(IDB_KEY_SONG);
      }
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
    db.close();
  } catch {
    // Ignore storage quota or private-browsing IndexedDB restrictions gracefully
  }
}

export async function loadMediaFilesFromIDB(): Promise<{
  visualClips: File[];
  songFile: File | null;
}> {
  try {
    const db = await openMediaDB();
    const result = await new Promise<{ visualClips: File[]; songFile: File | null }>(
      (resolve) => {
        const tx = db.transaction(IDB_STORE, "readonly");
        const store = tx.objectStore(IDB_STORE);
        const clipsReq = store.get(IDB_KEY_CLIPS);
        const songReq = store.get(IDB_KEY_SONG);

        let clipsDone = false;
        let songDone = false;
        let restoredClips: File[] = [];
        let restoredSong: File | null = null;

        const finishIfReady = () => {
          if (clipsDone && songDone) {
            resolve({ visualClips: restoredClips, songFile: restoredSong });
          }
        };

        clipsReq.onsuccess = () => {
          const raw = clipsReq.result;
          if (Array.isArray(raw)) {
            restoredClips = raw
              .map((r) => deserializeFile(r as StoredFileRecord))
              .filter((f): f is File => Boolean(f));
          }
          clipsDone = true;
          finishIfReady();
        };
        clipsReq.onerror = () => {
          clipsDone = true;
          finishIfReady();
        };

        songReq.onsuccess = () => {
          restoredSong = songReq.result
            ? deserializeFile(songReq.result as StoredFileRecord)
            : null;
          songDone = true;
          finishIfReady();
        };
        songReq.onerror = () => {
          songDone = true;
          finishIfReady();
        };
      }
    );
    db.close();
    return result;
  } catch {
    return { visualClips: [], songFile: null };
  }
}

export async function clearPersistedProject(): Promise<void> {
  try {
    if (typeof window !== "undefined" && window.localStorage) {
      window.localStorage.removeItem(PROJECT_STORAGE_KEY);
    }
    const db = await openMediaDB();
    await new Promise<void>((resolve) => {
      const tx = db.transaction(IDB_STORE, "readwrite");
      tx.objectStore(IDB_STORE).clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    });
    db.close();
  } catch {
    // Ignore storage cleanup failures
  }
}

