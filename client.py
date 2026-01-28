"""
Moltbot Gateway WebSocket 客户端
实现与 Moltbot Gateway 的 WebSocket 通信协议
"""
import asyncio
import json
import uuid
import sys
from typing import Any, Callable
from dataclasses import dataclass, field

import websockets
from websockets.client import WebSocketClientProtocol
from nonebot import logger


# 协议版本（必须与 Moltbot Gateway 匹配）
PROTOCOL_VERSION = 3


@dataclass
class PendingRequest:
    """等待响应的请求"""
    future: asyncio.Future
    expect_final: bool = False


@dataclass
class MoltbotClient:
    """Moltbot Gateway WebSocket 客户端"""
    
    url: str = "ws://127.0.0.1:18789"
    token: str | None = None
    password: str | None = None
    client_name: str = "gateway-client"  # 必须是 Moltbot 预定义的客户端 ID
    client_version: str = "1.0.0"
    
    # 内部状态
    ws: WebSocketClientProtocol | None = field(default=None, init=False)
    connected: bool = field(default=False, init=False)
    pending: dict[str, PendingRequest] = field(default_factory=dict, init=False)
    event_handlers: dict[str, list[Callable]] = field(default_factory=dict, init=False)
    _recv_task: asyncio.Task | None = field(default=None, init=False)
    _reconnect_task: asyncio.Task | None = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)
    _connect_nonce: str | None = field(default=None, init=False)
    
    async def connect(self) -> bool:
        """连接到 Moltbot Gateway"""
        if self._closed:
            return False
        
        # 确保旧连接完全关闭
        await self._cleanup_connection()
            
        try:
            self.ws = await websockets.connect(
                self.url,
                max_size=25 * 1024 * 1024,  # 25MB
            )
            
            # 立即发送连接请求（必须是第一个请求）
            hello = await self._send_connect()
            if hello:
                self.connected = True
                logger.info(f"Moltbot 连接成功: protocol={hello.get('protocol')}")
                # 连接成功后再启动消息接收任务
                self._recv_task = asyncio.create_task(self._receive_loop())
                return True
            else:
                logger.error("Moltbot 连接握手失败")
                await self._cleanup_connection()
                return False
                
        except Exception as e:
            logger.error(f"Moltbot 连接失败: {e}")
            await self._cleanup_connection()
            return False
    
    async def _cleanup_connection(self):
        """清理旧连接"""
        self.connected = False
        
        # 取消接收任务
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        self._recv_task = None
        
        # 关闭 WebSocket
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        
        # 清理所有等待中的请求
        for req_id, pending in list(self.pending.items()):
            if not pending.future.done():
                pending.future.set_exception(Exception("连接已关闭"))
        self.pending.clear()
    
    async def disconnect(self):
        """断开连接"""
        self._closed = True
        self.connected = False
        
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        
        if self.ws:
            await self.ws.close()
            self.ws = None
        
        # 清理所有等待中的请求
        for req_id, pending in self.pending.items():
            if not pending.future.done():
                pending.future.set_exception(Exception("连接已关闭"))
        self.pending.clear()
    
    async def _send_connect(self) -> dict | None:
        """发送连接握手请求（同步等待响应，不依赖接收循环）"""
        params = {
            "minProtocol": PROTOCOL_VERSION,
            "maxProtocol": PROTOCOL_VERSION,
            "client": {
                "id": self.client_name,
                "version": self.client_version,
                "platform": sys.platform,
                "mode": "backend",
            },
            "caps": [],
            "role": "operator",
            "scopes": ["operator.admin"],
        }
        
        # 添加认证信息
        if self.token or self.password:
            params["auth"] = {}
            if self.token:
                params["auth"]["token"] = self.token
            if self.password:
                params["auth"]["password"] = self.password
        
        req_id = str(uuid.uuid4())
        frame = {
            "type": "req",
            "id": req_id,
            "method": "connect",
            "params": params,
        }
        
        try:
            # 先检查是否有 connect.challenge 事件
            try:
                first_msg = await asyncio.wait_for(self.ws.recv(), timeout=2)
                first_data = json.loads(first_msg)
                
                if first_data.get("type") == "event" and first_data.get("event") == "connect.challenge":
                    # 收到挑战，提取 nonce
                    nonce = first_data.get("payload", {}).get("nonce")
                    if nonce:
                        logger.debug(f"收到 connect.challenge, nonce={nonce}")
                        # 暂不处理设备签名，直接发送 connect
            except asyncio.TimeoutError:
                # 没有挑战事件，直接发送 connect
                pass
            
            # 发送 connect 请求
            await self.ws.send(json.dumps(frame))
            
            # 等待响应
            response_raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
            response = json.loads(response_raw)
            
            if response.get("type") == "res" and response.get("id") == req_id:
                if response.get("ok"):
                    return response.get("payload")
                else:
                    error = response.get("error", {})
                    error_msg = error.get("message", "未知错误")
                    logger.error(f"连接握手失败: {error_msg}")
                    return None
            else:
                logger.error(f"连接握手收到意外响应: {response}")
                return None
                
        except asyncio.TimeoutError:
            logger.error("连接握手超时")
            return None
        except Exception as e:
            logger.error(f"连接握手失败: {e}")
            return None
    
    async def _receive_loop(self):
        """消息接收循环"""
        try:
            async for message in self.ws:
                await self._handle_message(message)
        except websockets.ConnectionClosed as e:
            logger.warning(f"Moltbot 连接关闭: {e}")
            self.connected = False
            if not self._closed:
                asyncio.create_task(self._reconnect())
        except Exception as e:
            logger.error(f"Moltbot 接收消息错误: {e}")
            self.connected = False
    
    async def _handle_message(self, raw: str):
        """处理收到的消息"""
        try:
            data = json.loads(raw)
            frame_type = data.get("type")
            
            if frame_type == "event":
                await self._handle_event(data)
            elif frame_type == "res":
                await self._handle_response(data)
            else:
                logger.debug(f"未知消息类型: {frame_type}")
                
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析错误: {e}")
    
    async def _handle_event(self, event: dict):
        """处理事件"""
        event_name = event.get("event")
        payload = event.get("payload")
        
        #logger.debug(f"收到事件: {event_name}, payload keys: {list(payload.keys()) if payload else []}")
        
        # 处理连接挑战
        if event_name == "connect.challenge":
            nonce = payload.get("nonce") if payload else None
            if nonce:
                self._connect_nonce = nonce
                # 重新发送连接请求
                asyncio.create_task(self._send_connect())
            return
        
        # 处理 tick 事件（心跳）
        if event_name == "tick":
            return
        
        # 调用注册的事件处理器
        handlers = self.event_handlers.get(event_name, [])
        for handler in handlers:
            try:
                await handler(payload)
            except Exception as e:
                logger.error(f"事件处理器错误 [{event_name}]: {e}")
        
        # 通用事件处理器
        all_handlers = self.event_handlers.get("*", [])
        for handler in all_handlers:
            try:
                await handler(event_name, payload)
            except Exception as e:
                logger.error(f"通用事件处理器错误: {e}")
    
    async def _handle_response(self, response: dict):
        """处理响应"""
        req_id = response.get("id")
        if not req_id or req_id not in self.pending:
            return
        
        pending = self.pending[req_id]
        
        # 检查是否是中间 ack
        payload = response.get("payload")
        if pending.expect_final and isinstance(payload, dict):
            if payload.get("status") == "accepted":
                return  # 继续等待最终响应
        
        # 完成请求
        del self.pending[req_id]
        
        if response.get("ok"):
            pending.future.set_result(payload)
        else:
            error = response.get("error", {})
            error_msg = error.get("message", "未知错误")
            pending.future.set_exception(Exception(error_msg))
    
    async def _reconnect(self):
        """重连逻辑"""
        if self._closed:
            return
            
        backoff = 1
        max_backoff = 30
        
        while not self._closed and not self.connected:
            logger.info(f"尝试重连 Moltbot... (等待 {backoff}s)")
            await asyncio.sleep(backoff)
            
            if self._closed:
                break
            
            if await self.connect():
                logger.info("Moltbot 重连成功")
                break
            
            backoff = min(backoff * 2, max_backoff)
    
    async def request(
        self,
        method: str,
        params: Any = None,
        timeout: float = 60,
        expect_final: bool = False,
    ) -> Any:
        """发送请求并等待响应"""
        if not self.ws:
            raise Exception("未连接到 Moltbot")
        
        req_id = str(uuid.uuid4())
        frame = {
            "type": "req",
            "id": req_id,
            "method": method,
            "params": params,
        }
        
        future = asyncio.get_event_loop().create_future()
        self.pending[req_id] = PendingRequest(future=future, expect_final=expect_final)
        
        try:
            await self.ws.send(json.dumps(frame))
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            if req_id in self.pending:
                del self.pending[req_id]
            raise Exception(f"请求超时: {method}")
        except Exception as e:
            if req_id in self.pending:
                del self.pending[req_id]
            raise
    
    def on_event(self, event_name: str, handler: Callable):
        """注册事件处理器"""
        if event_name not in self.event_handlers:
            self.event_handlers[event_name] = []
        self.event_handlers[event_name].append(handler)
    
    async def chat_send(
        self,
        session_key: str,
        message: str,
        thinking: str | None = None,
        timeout: float = 60,
        on_delta: Callable[[str], Any] | None = None,
        image_attachments: list[dict] | None = None,
    ) -> str | None:
        """
        发送聊天消息到 Moltbot 并等待响应
        
        Args:
            session_key: 会话标识
            message: 用户消息
            thinking: 思考模式 (off/low/high)
            timeout: 超时时间
            on_delta: 流式响应回调函数，接收每个 delta 文本片段
            image_attachments: 图片附件列表，每个元素包含 base64 和 mimeType
        
        Returns:
            AI 助手的响应文本
        """
        params = {
            "sessionKey": session_key,
            "message": message,
            "idempotencyKey": str(uuid.uuid4()),
        }
        
        if thinking:
            params["thinking"] = thinking
        
        # 添加图片附件
        if image_attachments:
            attachments = []
            for img in image_attachments:
                attachments.append({
                    "type": "image",
                    "mimeType": img.get("mimeType", "image/jpeg"),
                    "content": img.get("base64"),  # base64 编码的图片数据
                })
            params["attachments"] = attachments
            logger.debug(f"添加 {len(attachments)} 个图片附件")
        
        # 用于收集流式响应
        response_text = ""
        response_complete = asyncio.Event()
        response_error: Exception | None = None
        
        async def handle_chat_event(payload: dict):
            nonlocal response_text, response_error
            
            logger.debug(f"chat 事件处理: sessionKey={payload.get('sessionKey')}, state={payload.get('state')}")
            
            if payload.get("sessionKey") != session_key:
                logger.debug(f"会话不匹配，忽略: 期望 {session_key}, 收到 {payload.get('sessionKey')}")
                return
            
            state = payload.get("state")
            msg = payload.get("message")
            
            if state == "delta" and msg:
                # 流式响应片段
                content = msg.get("content", [])
                for item in content:
                    if item.get("type") == "text":
                        delta_text = item.get("text", "")
                        response_text = delta_text  # 保存最新的文本
                        logger.debug(f"收到 delta: {delta_text[:50]}...")
                        # 调用回调函数发送流式响应
                        if on_delta and delta_text:
                            try:
                                await on_delta(delta_text)
                            except Exception as e:
                                logger.error(f"on_delta 回调失败: {e}")
                
            elif state == "final":
                # 最终响应
                if msg:
                    content = msg.get("content", [])
                    for item in content:
                        if item.get("type") == "text":
                            response_text = item.get("text", "")
                logger.info(f"收到 final 响应，长度: {len(response_text)}")
                response_complete.set()
            
            elif state == "error":
                error_msg = payload.get("errorMessage", "未知错误")
                logger.error(f"chat 错误: {error_msg}")
                response_error = Exception(error_msg)
                response_complete.set()
            
            elif state == "aborted":
                logger.warning("chat 请求被中止")
                response_error = Exception("请求被中止")
                response_complete.set()
        
        # 注册临时事件处理器
        self.on_event("chat", handle_chat_event)
        
        try:
            # 发送请求（不等待 final，因为 Moltbot 会通过事件返回）
            logger.debug(f"发送 chat.send 请求: session={session_key}, message={message[:50]}...")
            result = await self.request("chat.send", params, timeout=10, expect_final=False)
            logger.debug(f"chat.send 请求返回: {result}")
            
            # 检查是否立即返回了错误
            if isinstance(result, dict):
                status = result.get("status")
                if status == "error":
                    error_msg = result.get("summary", "未知错误")
                    raise Exception(f"chat.send 失败: {error_msg}")
            
            # 等待 chat 事件中的最终响应
            logger.debug(f"等待 chat 事件响应...")
            await asyncio.wait_for(response_complete.wait(), timeout=timeout)
            
            if response_error:
                raise response_error
            
            return response_text.strip() if response_text else None
            
        except asyncio.TimeoutError:
            logger.error(f"等待 chat 响应超时，未收到 final 事件")
            raise
        finally:
            # 移除事件处理器
            if "chat" in self.event_handlers:
                try:
                    self.event_handlers["chat"].remove(handle_chat_event)
                except ValueError:
                    pass
    
    async def agent_send(
        self,
        message: str,
        session_key: str | None = None,
        agent_id: str | None = None,
        thinking: str | None = None,
        timeout: float = 120,
    ) -> str | None:
        """
        调用 Moltbot Agent
        
        Args:
            message: 用户消息
            session_key: 会话标识
            agent_id: Agent ID
            thinking: 思考模式
            timeout: 超时时间
        
        Returns:
            Agent 响应文本
        """
        params = {
            "message": message,
            "idempotencyKey": str(uuid.uuid4()),
        }
        
        if session_key:
            params["sessionKey"] = session_key
        if agent_id:
            params["agentId"] = agent_id
        if thinking:
            params["thinking"] = thinking
        
        try:
            result = await self.request("agent", params, timeout=timeout, expect_final=True)
            
            # 从结果中提取文本
            if isinstance(result, dict):
                text = result.get("text") or result.get("response")
                if text:
                    return str(text).strip()
            
            return None
            
        except Exception as e:
            logger.error(f"Agent 请求失败: {e}")
            raise
    
    async def send_message(
        self,
        to: str,
        message: str,
        channel: str | None = None,
        account_id: str | None = None,
    ) -> dict | None:
        """
        发送消息到指定目标
        
        Args:
            to: 目标（手机号/用户ID等）
            message: 消息内容
            channel: 渠道 (whatsapp/telegram/etc)
            account_id: 账户ID
        
        Returns:
            发送结果
        """
        params = {
            "to": to,
            "message": message,
            "idempotencyKey": str(uuid.uuid4()),
        }
        
        if channel:
            params["channel"] = channel
        if account_id:
            params["accountId"] = account_id
        
        return await self.request("send", params)
