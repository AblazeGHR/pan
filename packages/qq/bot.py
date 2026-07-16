"""Pan QQ Channel entry point — start NoneBot2 and load QQ plugin."""

from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter
import nonebot

nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)
nonebot.load_plugin("plugin")
nonebot.run()
