// Runs inside the disposable Zotero only after the external identity barrier.
async function main(ctx) {
  const { Zotero, Services, IOUtils, win, plugin } = ctx;
  const expectedProfile = ctx.env('ACP_ZOTERO_PROFILE');
  const expectedData = ctx.env('ACP_ZOTERO_DATA');
  const actualProfile = Zotero.Profile.dir.path || String(Zotero.Profile.dir);
  const actualData = Zotero.DataDirectory.dir;
  const canonical = s => String(s).replaceAll('\\', '/').replace(/\/$/, '').toLowerCase();
  const identity = { pid:Services.appinfo.processID, profile:actualProfile, data:actualData };
  await IOUtils.writeUTF8(ctx.env('ACP_ZOTERO_IDENTITY'), JSON.stringify(identity));
  if (canonical(actualProfile) !== canonical(expectedProfile) || canonical(actualData) !== canonical(expectedData))
    throw new Error('disposable profile or data directory does not match');
  await ctx.waitFor(() => IOUtils.exists(ctx.env('ACP_ZOTERO_APPROVED')), 'external process verification', 60000);

  const parent = new Zotero.Item('journalArticle');
  parent.setField('title', 'Synthetic ACP isolation paper');
  parent.setCreators([{creatorType:'author', firstName:'Test', lastName:'Fixture'}]);
  parent.setField('date', '2026');
  await parent.saveTx();
  win.ZoteroPane.selectItem(parent.id);
  plugin.windows.get(win).show();
  const root = await ctx.waitFor(() => win.document.getElementById('zmc-root')?.shadowRoot?.querySelector('textarea')
    && win.document.getElementById('zmc-root').shadowRoot, 'chat panel', 30000);
  const host = plugin.panel().host;
  const cfg = {label:'Pan isolated', command:ctx.env('ACP_PYTHON'),
    args:['-m','packages.core.cli','--root',ctx.env('ACP_PAN_ROOT'),'acp','--profile','editor'],
    env:{PYTHONPATH:ctx.env('ACP_SOURCE_ROOT')}};
  await host.setSettings({backend:'custom', customAgent:cfg, chatFolder:ctx.env('ACP_WORKSPACE'),
                          followFocus:true, welcomed:true});
  const saved = host.getSettings();
  if (saved.backend !== 'custom' || JSON.stringify(saved.customAgent) !== JSON.stringify(cfg))
    throw new Error('Custom command was not saved');
  await ctx.waitFor(() => host.currentContext().some(c => c.kind === 'item' && c.label.includes('Fixture')),
                    'synthetic paper context', 20000);
  const ta = root.querySelector('textarea');
  ta.value = 'Summarize this synthetic paper.';
  ta.dispatchEvent(new win.Event('input', {bubbles:true}));
  await ctx.waitFor(() => !root.querySelector('button.send')?.disabled, 'send enabled', 20000);
  root.querySelector('button.send').click();
  await ctx.waitFor(() => root.textContent.includes('ack:'), 'synthetic answer rendered', 45000);
  await ctx.waitFor(() => !root.querySelector('button.send')?.classList.contains('send--stop'), 'turn terminal', 30000);
  return {identity, savedCustom:true, paperContext:true, answerVisible:true, referenceOpened:false};
}
