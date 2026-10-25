/**
 * System (desktop) notification for the user Locker.
 *
 * The Locker badge already polls one aggregate unread summary every 30s. This
 * module turns a strict increase in that total into exactly one desktop
 * notification, and only when the user asked for it (App Settings →
 * Notifications → Locker new messages), the browser permission is granted, and
 * the page is not visible. The first observation of a polling lifetime only
 * records the baseline, so opening Pan never reports pre-existing unread items;
 * a flat or decreasing total (messages read) never notifies either.
 *
 * The notification carries the stable `pan-locker-unread` tag, which replaces
 * an earlier Locker notification instead of stacking duplicates. The existing
 * `worker.result` browser notification is a different event and is untagged, so
 * the two never share a tag or a code path.
 */
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useUIStore } from '@/stores/uiStore';

export const LOCKER_NOTIFICATION_TAG = 'pan-locker-unread';
export const LOCKER_NOTIFICATION_TITLE = 'Pan Locker';

export type LockerNotificationPermission = NotificationPermission | 'unsupported';

function notificationApi(): typeof Notification | null {
  // `typeof` on a possibly undeclared global is safe; some embeds drop the API.
  return typeof Notification === 'undefined' ? null : Notification;
}

/** Current browser notification permission, or 'unsupported' without the API. */
export function lockerNotificationPermission(): LockerNotificationPermission {
  return notificationApi()?.permission ?? 'unsupported';
}

/**
 * Ask the browser for notification permission. Must run inside the switch's
 * user gesture; a rejection or a refused prompt resolves to 'denied' instead of
 * throwing into the click handler.
 */
export async function requestLockerNotificationPermission(): Promise<LockerNotificationPermission> {
  const api = notificationApi();
  if (!api) return 'unsupported';
  try {
    return await api.requestPermission();
  } catch {
    return 'denied';
  }
}

let baseline: number | null = null;

/** Drop the baseline when the shared poll stops, so a remount re-baselines. */
export function resetLockerUnreadNotificationBaseline(): void {
  baseline = null;
}

function openLocker(): void {
  try {
    window.focus();
  } catch {
    // Focus is best effort; navigation below still runs.
  }
  // Lazy import keeps the router out of this module's static graph (the same
  // pattern as the Session deep link), so a util import cannot cycle.
  void import('@/router')
    .then(({ router }) => router.navigate('/locker'))
    .catch(() => {
      useUIStore.getState().showToast('无法打开 Locker，请手动切换', 'warning');
    });
}

/**
 * Feed one aggregate unread total from the shared Locker poll. Notifies at most
 * once per call — one poll round can only ever produce one notification.
 */
export function observeLockerUnreadTotal(total: number): void {
  if (baseline === null) {
    baseline = total;
    return;
  }
  const previous = baseline;
  baseline = total;
  if (total <= previous) return;
  if (!useAppSettingsStore.getState().notifications.lockerDesktop) return;
  if (typeof document !== 'undefined' && document.visibilityState === 'visible') return;
  const api = notificationApi();
  if (!api || api.permission !== 'granted') return;
  try {
    const notification = new api(LOCKER_NOTIFICATION_TITLE, {
      body: `有 ${total - previous} 条新消息`,
      tag: LOCKER_NOTIFICATION_TAG,
    });
    // Click → focus Pan and jump to the Locker view.
    notification.onclick = () => {
      openLocker();
      notification.close();
    };
  } catch {
    // A platform that refuses construction must not break the unread poll.
  }
}
