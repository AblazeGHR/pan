"""QQ Bridge 入口 — 启动 NoneBot2 并加载插件。"""

from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter
import nonebot

nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)
nonebot.load_plugin("plugin")
nonebot.run()
