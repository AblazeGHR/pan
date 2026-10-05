import { useEffect, useState } from 'react';

// Route content must not hold up the shell and its first Session summary read.
// Start downloading in parallel with shell rendering. A normal state update
// avoids Suspense's minimum fallback window on this first interactive route.
const chatModule = import('./ChatView').then(
  (module) => ({ View: module.default, error: null }),
  (error: unknown) => ({ View: null, error }),
);
let cachedChat: Awaited<typeof chatModule> | null = null;
void chatModule.then((module) => { cachedChat = module; });
export default function ChatRoute() {
  const [loaded, setLoaded] = useState<Awaited<typeof chatModule> | null>(() => cachedChat);
  useEffect(() => {
    let active = true;
    void chatModule.then((module) => { if (active) setLoaded(module); });
    return () => { active = false; };
  }, []);
  if (loaded?.error) throw loaded.error;
  const View = loaded?.View;
  return View ? <View /> : <div className="p-4 text-sm text-text-tertiary">Loading...</div>;
}
