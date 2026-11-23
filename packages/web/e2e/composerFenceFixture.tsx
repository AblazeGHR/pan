import { createRoot } from 'react-dom/client';
import { RichTextComposer } from '../src/components/chat/RichTextComposer';
import '../src/index.css';

const params = new URLSearchParams(location.search);
const withToken = params.has('token');
createRoot(document.getElementById('root')!).render(<div style={{height: 300}}>
  <RichTextComposer initialText={params.get('draft') || ''}
    initialValue={withToken ? {parts:[{type:'text',value:'before\n'}, {type:'attachment',attachmentId:'a1'}, {type:'text',value:' after'}],text:'before\n after',occurrenceIds:['a1'],attachmentIds:['a1']} : undefined}
    attachments={withToken ? [{id:'a1',displayName:'fixture.txt'}] : []} onAttachmentDrop={() => null} onRemoveAttachment={() => {}}
    onChange={(value) => { document.getElementById('value')!.textContent = JSON.stringify(value); }} />
  <pre id="value" />
</div>);
