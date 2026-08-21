"""Pan QQ Channel entry point — start NoneBot2 and load QQ plugin."""

from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter
import nonebot

# Mixed driver: fastapi serves the QQ HTTP API (server_app) while websockets
# provides the OneBot v11 WS *client* connection to NapCat. Without the WS
# driver, OneBot V11 cannot receive/send messages (仅 fastapi 不支持 WS client).
nonebot.init(driver="~fastapi+~websockets")
driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)
nonebot.load_plugin("plugin")
nonebot.run()
