import { create } from 'zustand';
import type {
  ScheduledTask,
  ScheduledTaskInput,
  ScheduledTaskPatch,
  SchedulerStatus,
} from '@/types';
import {
  createScheduledTask,
  deleteScheduledTask,
  fetchScheduledTasks,
  fetchSchedulerStatus,
  pauseScheduledTask,
  resumeScheduledTask,
  runScheduledTaskNow,
  updateScheduledTask,
} from '@/services/api';
import { useUIStore } from '@/stores/uiStore';

interface ScheduleStore {
  tasks: ScheduledTask[];
  /** True once the first list GET finished (success or failure). */
  loaded: boolean;
  loading: boolean;
  /** Id of the task a mutation is in flight for; blocks row re-entry. */
  busyId: string | null;
  /** Engine health snapshot from GET /api/scheduler/status. */
  status: SchedulerStatus | null;

  loadTasks: () => Promise<void>;
  loadStatus: () => Promise<void>;
  createTask: (input: ScheduledTaskInput) => Promise<boolean>;
  updateTask: (taskId: string, patch: ScheduledTaskPatch) => Promise<boolean>;
  remove: (taskId: string) => Promise<void>;
  toggleEnabled: (taskId: string, enabled: boolean) => Promise<void>;
  pause: (taskId: string) => Promise<void>;
  resume: (taskId: string) => Promise<void>;
  runNow: (taskId: string) => Promise<void>;
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export const useScheduleStore = create<ScheduleStore>((set, get) => {
  const toast = (text: string, type: 'info' | 'error' = 'info') =>
    useUIStore.getState().showToast(text, type);

  /** Merge the server's authoritative task back into the list. */
  const upsert = (task: ScheduledTask) =>
    set((state) => {
      const index = state.tasks.findIndex((t) => t.id === task.id);
      const tasks =
        index < 0
          ? [...state.tasks, task]
          : state.tasks.map((t, i) => (i === index ? task : t));
      return { tasks };
    });

  /** Run one mutation against a single task: busy-guard, toast, reload. */
  const act = async (
    taskId: string,
    fallback: string,
    run: () => Promise<ScheduledTask>,
    okText?: string,
  ): Promise<void> => {
    if (get().busyId) return;
    set({ busyId: taskId });
    try {
      const task = await run();
      upsert(task);
      if (okText) toast(okText);
      await get().loadTasks();
    } catch (error) {
      toast(message(error, fallback), 'error');
    } finally {
      set({ busyId: null });
    }
  };

  return {
    tasks: [],
    loaded: false,
    loading: false,
    busyId: null,
    status: null,

    loadTasks: async () => {
      set({ loading: true });
      try {
        const tasks = await fetchScheduledTasks();
        set({ tasks });
      } catch (error) {
        // Keep the previous list so a transient failure doesn't blank the UI.
        toast(message(error, 'Failed to load scheduled tasks'), 'error');
      } finally {
        set({ loading: false, loaded: true });
      }
    },

    loadStatus: async () => {
      try {
        const status = await fetchSchedulerStatus();
        set({ status });
      } catch {
        // Engine health is decorative — a failure must not surface as a toast.
        set({ status: null });
      }
    },

    createTask: async (input) => {
      if (get().busyId) return false;
      set({ busyId: '__new__' });
      try {
        const task = await createScheduledTask(input);
        upsert(task);
        toast(`Scheduled "${task.name}"`);
        await get().loadTasks();
        return true;
      } catch (error) {
        toast(message(error, 'Failed to create task'), 'error');
        return false;
      } finally {
        set({ busyId: null });
      }
    },

    updateTask: async (taskId, patch) => {
      if (get().busyId) return false;
      set({ busyId: taskId });
      try {
        const task = await updateScheduledTask(taskId, patch);
        upsert(task);
        toast('Task updated');
        await get().loadTasks();
        return true;
      } catch (error) {
        toast(message(error, 'Failed to update task'), 'error');
        return false;
      } finally {
        set({ busyId: null });
      }
    },

    remove: async (taskId) => {
      if (get().busyId) return;
      set({ busyId: taskId });
      try {
        await deleteScheduledTask(taskId);
        set((state) => ({ tasks: state.tasks.filter((t) => t.id !== taskId) }));
        toast('Task deleted');
        await get().loadTasks();
      } catch (error) {
        toast(message(error, 'Failed to delete task'), 'error');
      } finally {
        set({ busyId: null });
      }
    },

    toggleEnabled: async (taskId, enabled) => {
      await act(
        taskId,
        'Failed to toggle task',
        () => updateScheduledTask(taskId, { enabled }),
        enabled ? 'Task enabled' : 'Task disabled',
      );
    },

    pause: async (taskId) => {
      await act(taskId, 'Failed to pause task', () => pauseScheduledTask(taskId), 'Task paused');
    },

    resume: async (taskId) => {
      await act(taskId, 'Failed to resume task', () => resumeScheduledTask(taskId), 'Task resumed');
    },

    runNow: async (taskId) => {
      if (get().busyId) return;
      set({ busyId: taskId });
      try {
        await runScheduledTaskNow(taskId);
        toast('Task dispatched');
        await get().loadTasks();
      } catch (error) {
        toast(message(error, 'Failed to run task'), 'error');
      } finally {
        set({ busyId: null });
      }
    },
  };
});
