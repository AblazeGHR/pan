# AttachmentRef / message parts protocol

本阶段采用 A + C：浏览器只上传普通文件内容，消息边界统一使用 session-scoped opaque `attachmentId` 与结构化 `parts`。目录递归上传不在本阶段范围内。

## 请求类型

```ts
type MessagePart =
  | { type: "text"; text: string }
  | {
      type: "attachment";
      attachmentId: string;
      displayName?: string; // hint only; server canonicalizes it
      mimeType?: string;    // hint only
      size?: number;        // hint only
      source?: "upload" | "server_file"; // hint only
    };

POST /api/sessions/{sessionId}/queue {
  text: string,                 // legacy/adapter fallback; optional with parts
  parts?: MessagePart[],
  clientMessageId?: string
}
```

The WebSocket `user_inject` envelope accepts the same `text`/`parts` pair. When
`parts` is present, the server validates each opaque id, ignores client labels,
hrefs and paths, and generates the canonical Markdown fallback from the
session-owned registry. A text-only request keeps the old Markdown protocol and
the existing `@"path"` compatibility normalization.

The durable queue task and Session history retain both fields:

```json
{
  "text": "前置 [文件.txt](/api/attachments/...) 后置",
  "parts": [
    {"type":"text","text":"前置 "},
    {"type":"attachment","attachmentId":"upload_...","displayName":"文件.txt"},
    {"type":"text","text":" 后置"}
  ]
}
```

Text-only adapters consume `text`; parts stay durable so history, retry,
restart and a future native adapter can recover attachment identity and editor
position. Queue delivery events include parts as well as the compatibility
content string.

## Attachment identity and validation

Client uploads are written to a session-isolated attachment directory through
the existing raw-byte upload endpoint. Server-file selections are registered
through `POST /api/sessions/{sessionId}/attachments/from-server-file`; the
server validates the file against the Session workdir and stores the mapping in
the session attachment registry. Both sources return an opaque id. The server
accepts an id only when it belongs to the target Session, the registry says the
operation is complete, and the authoritative file still exists. Missing files
are stale; incomplete entries are rejected. A client cannot make an arbitrary
`href`, absolute path, display name or query string authoritative.

Existing Markdown attachment links remain accepted and are validated by their
session query/path rules. Existing message links can be dragged back into the
composer; structured history supplies the opaque id when available, while old
upload links retain the conservative storage-filename compatibility path.

## Browser intake and lifecycle

The editor checks Pan's custom attachment MIME first. Valid Pan payloads are
handled as attachment references and do not fall through to text insertion.
Only after that does it inspect `DataTransfer.files`/`FileList`; file bytes are
uploaded and browser fake paths are ignored. Paste/drop of a directory,
directory entry/handle, or file URI is rejected with a user-facing message; no
recursive enumeration is attempted. A dropped/pasted file is inserted at the
editor caret as an attachment node, including middle positions. Unembedded
chips are appended to the ordinary text tail on Send.

The existing upload state machine remains the single implementation for picker,
paste and drop: byte progress, cancel, retry, deduplication, failed-send
retention and session-switch aborts all apply equally. `?mock=1` uses the same
state transitions and response shapes in memory and never writes real
persistent files.
