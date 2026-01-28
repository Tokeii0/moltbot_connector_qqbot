"""
Moltbot Connector - NoneBot2 插件
连接到 Moltbot Gateway，实现 QQ 与 Moltbot AI 助手的桥接
"""
import asyncio
from nonebot import get_driver, on_message, logger
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, PrivateMessageEvent, Message, MessageSegment
from nonebot.rule import to_me
from nonebot.plugin import PluginMetadata
from nonebot.exception import FinishedException

from .client import MoltbotClient
from .config import Config

__plugin_meta__ = PluginMetadata(
    name="Moltbot Connector",
    description="连接 Moltbot AI 助手的 NoneBot2 插件",
    usage="@机器人 <消息> 或私聊发送消息",
    type="application",
    config=Config,
)

driver = get_driver()
plugin_config = Config.model_validate(driver.config.model_dump())

# Moltbot 客户端实例
moltbot_client: MoltbotClient | None = None


@driver.on_startup
async def startup():
    """启动时连接 Moltbot Gateway"""
    global moltbot_client
    moltbot_client = MoltbotClient(
        url=plugin_config.moltbot_gateway_url,
        token=plugin_config.moltbot_token,
        password=plugin_config.moltbot_password,
    )
    await moltbot_client.connect()
    logger.info(f"Moltbot Connector 已连接到 {plugin_config.moltbot_gateway_url}")


@driver.on_shutdown
async def shutdown():
    """关闭时断开连接"""
    global moltbot_client
    if moltbot_client:
        await moltbot_client.disconnect()
        logger.info("Moltbot Connector 已断开连接")


# 处理 @机器人 的消息
moltbot_handler = on_message(rule=to_me(), priority=10, block=True)


@moltbot_handler.handle()
async def handle_message(bot: Bot, event: MessageEvent):
    """处理收到的消息，转发给 Moltbot"""
    global moltbot_client
    
    # 检查用户权限
    if plugin_config.moltbot_allowed_users:
        if event.user_id not in plugin_config.moltbot_allowed_users:
            logger.debug(f"用户 {event.user_id} 不在白名单中，忽略消息")
            return
    
    if not moltbot_client or not moltbot_client.connected:
        await moltbot_handler.finish("Moltbot 未连接，请稍后再试")
        return
    
    # 获取纯文本消息
    message_text = event.get_plaintext().strip()
    
    # 提取图片 URL 并附加到消息文本中
    image_urls = []
    for seg in event.message:
        if seg.type == "image":
            # 获取图片 URL
            url = seg.data.get("url") or seg.data.get("file")
            if url:
                image_urls.append(url)
                logger.debug(f"提取到图片 URL: {url}")
    
    # 将图片 URL 附加到消息文本中
    if image_urls:
        url_text = "\n".join([f"[图片: {url}]" for url in image_urls])
        if message_text:
            message_text = f"{message_text}\n\n{url_text}"
        else:
            message_text = url_text
    
    # 如果没有文本也没有图片，则忽略
    if not message_text:
        return
    
    # 构建会话标识
    if isinstance(event, GroupMessageEvent):
        session_key = f"qq:group:{event.group_id}:{event.user_id}"
    else:
        session_key = f"qq:private:{event.user_id}"
    
    try:
        # 发送消息到 Moltbot 并获取响应（不使用流式输出）
        response = await moltbot_client.chat_send(
            session_key=session_key,
            message=message_text,
            timeout=plugin_config.moltbot_timeout,
        )
        
        if response:
            await moltbot_handler.finish(response)
        else:
            await moltbot_handler.finish("Moltbot 没有返回响应")
    
    except FinishedException:
        raise  # 不捕获 NoneBot 的 FinishedException
    except asyncio.TimeoutError:
        logger.error(f"Moltbot 请求超时 (session: {session_key})")
        await moltbot_handler.finish("请求超时，Moltbot 未在规定时间内响应")
    except Exception as e:
        logger.error(f"Moltbot 请求失败: {e}", exc_info=True)
        await moltbot_handler.finish(f"请求失败: {str(e)}")
