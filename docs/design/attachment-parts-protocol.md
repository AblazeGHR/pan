# AttachmentRef / message parts protocol

本阶段采用 A + C：浏览器只上传普通文件内容，消息边界统一使用 session-scoped opaque `attachmentId` 与结构化 `parts`。目录递归上传不在本阶段范围内。服务端文件和已完成客户端上传都以服务端实时 canonical absolute path 作为 Worker/adapter 投影目标；浏览器只接触安全 editor/download href、opaque id 和显示名。

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
      line?: number;
      endLine?: number;
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
session-owned registry. The durable part additionally carries an internal-only
`__serverPath` that is stripped from queue/history/WS API views and used only by
the Worker projection. A text-only request keeps the old Markdown protocol and
the existing `@"path"` compatibility normalization; when it contains a
validated Pan attachment link, the server upgrades that link to durable parts
before queue persistence so the Worker still receives a canonical path.

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

Text-only adapters consume the Worker-projected text (for example
`@"D:\\project\\docs\\file.txt"`), never an API href. Parts stay durable so
history, retry, restart and a future native adapter can recover attachment
identity and editor position. Queue delivery events include public parts as
well as the compatibility content string.

## Attachment identity and validation

Client uploads are written to a session-isolated attachment directory through
the existing raw-byte upload endpoint. Server-file selections are registered
through `POST /api/sessions/{sessionId}/attachments/from-server-file`; the
server validates the file against the Session workdir and stores the mapping in
the session attachment registry. Both sources return an opaque id. The server
accepts an id only when the registry owner/capability is authorized for the
target Session, the operation is complete, and the authoritative file still
exists. A cross-Session drop imports a registry receipt into the target without
copying bytes; the source Session is not changed. Missing files are stale;
incomplete entries are rejected. A client cannot make an arbitrary `href`,
absolute path, display name or query string authoritative.

The server projects existing, resolvable local Markdown history links to
`/api/attachments/editor/{opaqueId}?session_id=...#Lx-Ly`. Clicking this safe
editor href resolves metadata and opens the current Editor at the optional line
range; dragging it emits `application/x-pan-attachment` with only
`serverAttachmentId`, source Session metadata, safe opaque download href,
displayName and optional range. The drag payload contains no absolute path.
Old Markdown that cannot be resolved remains click-only compatibility: it can be
opened in the current Session using the existing path parser, but it has no safe
opaque capability to drag across Sessions until the server can resolve it.

## Browser intake and lifecycle

The editor checks Pan's custom attachment MIME first. Valid Pan payloads are
handled as attachment references and do not fall through to text insertion.
Only after that does it inspect `DataTransfer.files`/`FileList`; file bytes are
uploaded and browser fake paths are ignored. Paste/drop of a directory,
directory entry/handle, or file URI is rejected with a user-facing message; no
recursive enumeration is attempted. A dropped/pasted file is inserted at the
editor caret as an attachment node, including middle positions. Unembedded
chips are appended to the ordinary text tail on Send.

The exact `application/x-pan-attachment` drag payload is:

```json
{
  "displayName": "readme.md",
  "href": "/api/attachments/ref/att_...?...",
  "serverAttachmentId": "att_...",
  "sourceSessionId": "source-session",
  "source": "message",
  "location": { "line": 4, "endLine": 8 }
}
```

`href` must be a Pan-generated opaque `attachments/ref` download route;
`serverAttachmentId`, `sourceSessionId`, `source`, and `location` are metadata.
The payload never contains `path`, a client absolute path, a file URI, or a
Windows/UNC path. T-027.1 must read this MIME before native file/URI handling,
reject a payload whose `sourceSessionId` is not the current input Session when
the source is an input chip or inline composer node, and for a message/editor
source send the opaque `serverAttachmentId` as a structured attachment part.
The queue request remains `{ text, parts }`; `parts[].attachmentId` is the only
authoritative attachment field, while labels, hrefs, and paths are hints or
ignored. A cross-Session message drop must not upload bytes.

The existing upload state machine remains the single implementation for picker,
paste and drop: byte progress, cancel, retry, deduplication, failed-send
retention and session-switch aborts all apply equally. `?mock=1` uses the same
state transitions and response shapes in memory and never writes real
persistent files.
