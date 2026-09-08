"""Pan WeChat Channel — iLink (微信 ClawBot) bridge to Pan Core.

Communicates exclusively via Core HTTP/WS API.
No internal imports from packages.core.

v1 只支持纯文本消息（媒体需 AES-128-ECB 加解密 + CDN 上传，不在此范围）。
"""
