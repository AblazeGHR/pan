import {
  fetchAdapterConfig,
  fetchCliStatus,
  fetchNewSessionDefaults,
  fetchSessionTemplates,
} from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { getCreationWorkspaceIds } from '@/utils/creationWorkspace';
import { nextSessionDefaultName } from '@/utils/sessionName';

let creationInFlight: Promise<void> | null = null;

/** Create a direct New Session using the saved New Session form defaults. */
export function createQuickNewSession(): Promise<void> {
  if (creationInFlight) return creationInFlight;
  const request = (async () => {
    const store = useSessionStore.getState();
    const name = nextSessionDefaultName(store.sessions);
    // Capture the active scope at click time, before any configuration reads.
    const activeWorkspaceId = useUIStore.getState().activeWorkspaceId;
    const [workspaceIds, defaults] = await Promise.all([
      getCreationWorkspaceIds(activeWorkspaceId),
      fetchNewSessionDefaults(),
    ]);

    if (!defaults) {
      await store.createNewSession(name, undefined, undefined, undefined, { workspaceIds });
      return;
    }

    const [cliStatus, templates] = await Promise.all([
      fetchCliStatus(),
      fetchSessionTemplates(),
    ]);
    const adapter = cliStatus.adapters.find((candidate) => candidate.name === defaults.adapter);
    if (!adapter?.available) {
      throw new Error(`已保存的默认 adapter「${defaults.adapter}」当前不可用，未创建 Session。请在 New Session 中更新默认配置。`);
    }

    const templateName = defaults.sessionTemplate;
    if (templateName) {
      const template = templates.find((candidate) => candidate.name === templateName);
      if (!template) {
        throw new Error(`已保存的 Session Template「${templateName}」已不可用，未创建 Session。请在 New Session 中更新默认配置。`);
      }
      if (template.adapter && template.adapter !== defaults.adapter) {
        throw new Error(`已保存的 Session Template 要求 adapter「${template.adapter}」，与默认 adapter「${defaults.adapter}」不一致，未创建 Session。请在 New Session 中更新默认配置。`);
      }
    }

    const config = await fetchAdapterConfig(defaults.adapter);
    const executionModes = config.executionModes || ['stream'];
    let outputMode = defaults.outputMode;
    if (outputMode && (executionModes.length < 2 || !executionModes.includes(outputMode))) {
      throw new Error(`已保存的 Output Mode「${outputMode}」不适用于 adapter「${defaults.adapter}」，未创建 Session。请在 New Session 中更新默认配置。`);
    }
    // Match the form's behavior when the saved mode is blank but this adapter
    // now offers multiple modes: select its first supported mode.
    if (!outputMode && executionModes.length > 1) outputMode = executionModes[0] || 'stream';

    await store.createNewSession(
      name,
      defaults.workdir || null,
      defaults.adapter,
      templateName || undefined,
      { outputMode: outputMode || undefined, workspaceIds },
    );
  })();
  const sharedRequest = request.finally(() => {
    if (creationInFlight === sharedRequest) creationInFlight = null;
  });
  creationInFlight = sharedRequest;
  return sharedRequest;
}
