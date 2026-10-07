import { useEffect, useState, type ComponentType } from 'react';

/** Load only a visible route. Commit it as soon as its module is ready, without
 * Suspense's minimum fallback window delaying an otherwise ready first view. */
export function deferredView(load: () => Promise<{ default: ComponentType }>, preload = false) {
  type Loaded = { View: ComponentType | null; error: unknown };
  let pending: Promise<Loaded> | null = null;
  let cached: Loaded | null = null;
  const getModule = () => {
    if (!pending) {
      pending = load().then(
        (module) => ({ View: module.default, error: null }),
        (error: unknown) => ({ View: null, error }),
      );
      void pending.then((module) => { cached = module; });
    }
    return pending;
  };
  if (preload) void getModule();
  return function DeferredView() {
    const [loaded, setLoaded] = useState<Loaded | null>(() => {
      void getModule();
      return cached;
    });
    useEffect(() => {
      let active = true;
      void getModule().then((module) => { if (active) setLoaded(module); });
      return () => { active = false; };
    }, []);
    if (loaded?.error) throw loaded.error;
    const View = loaded?.View;
    return View ? <View /> : <div className="p-4 text-sm text-text-tertiary">Loading...</div>;
  };
}
