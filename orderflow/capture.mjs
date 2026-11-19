// Session observations can survive frame changes and page reloads. Never synthesize missing depth.
let database;
function open() {
  if (!database)
    database = new Promise((resolve, reject) => {
      const request = indexedDB.open('fibstein-observations', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('markets');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  return database;
}
export async function readCapture(key) {
  try {
    const db = await open();
    return await new Promise((resolve, reject) => {
      const request = db.transaction('markets').objectStore('markets').get(key);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } catch {
    return null;
  }
}
export async function writeCapture(key, snapshot) {
  try {
    const db = await open(),
      store = db.transaction('markets', 'readwrite').objectStore('markets');
    store.put(snapshot, key);
  } catch {
    /* The current tab still has its capture when browser storage is unavailable. */
  }
}
