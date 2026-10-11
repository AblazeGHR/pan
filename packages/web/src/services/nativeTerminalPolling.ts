// One output reader per mounted terminal. Await xterm's write callback before
// reading again; full transport chunks indicate that catch-up may be needed.
export function createNativeTerminalPoller(
  read: () => Promise<number | null>,
  visible: () => boolean = () => document.visibilityState !== 'hidden',
) {
  let stopped = false;
  let reading = false;
  let wakePending = false;
  let idleDelay = 80;
  let activeUntil = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  function schedule(delay: number) {
    clearTimeout(timer);
    timer = undefined;
    if (!stopped && visible()) timer = setTimeout(() => void poll(), delay);
  }
  async function poll() {
    if (stopped || reading || !visible()) return;
    reading = true;
    let bytes: number | null;
    try { bytes = await read(); }
    finally { reading = false; }
    if (stopped) return;
    if (bytes === null) { stopped = true; return; }
    if (bytes > 0) activeUntil = Date.now() + 1000;
    idleDelay = bytes > 0 || Date.now() < activeUntil ? 80 : Math.min(320, idleDelay * 2);
    const delay = wakePending || bytes >= 65536 ? 0 : idleDelay;
    wakePending = false;
    schedule(delay);
  }
  return {
    wake() {
      if (stopped) return;
      idleDelay = 80;
      activeUntil = Date.now() + 1000;
      if (reading) { wakePending = true; return; }
      schedule(0);
    },
    pause() { clearTimeout(timer); timer = undefined; },
    stop() { stopped = true; clearTimeout(timer); timer = undefined; },
  };
}
